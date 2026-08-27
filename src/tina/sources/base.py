"""The source adapter contract."""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from tina.errors import TinaError
from tina.log import get_logger
from tina.models import WorkItem

log = get_logger(__name__)


class ClaimPrognosis(BaseModel):
    """What `claim` would do right now, established without doing it.

    The adapter owns the verdict because claim semantics differ: Jira's
    compare-and-set refuses any existing assignee, including the bot itself,
    while GitHub's idempotent add succeeds when the bot is already the sole
    one. A caller that decided this for itself would have to know both.

    `holder` is `""` when nobody holds the item — the same convention the
    dispatch log line uses for a missing URL, rather than a null.
    """

    model_config = ConfigDict(frozen=True)

    would_claim: bool
    holder: str = ""


@runtime_checkable
class Source(Protocol):
    """A ticket tracker, seen through a query and a claim.

    Tina never inspects the content of a work item — it only knows the item
    matched a query. All judgment about what the item is happens in the track.
    """

    def query(self, q: str) -> list[WorkItem]:
        """Run the configured query and return matching items."""
        ...

    def get(self, item_id: str) -> WorkItem:
        """Fetch a single item by tracker identifier."""
        ...

    def matches(self, item_id: str, q: str) -> bool:
        """Whether the item still matches the configured query, right now.

        The worker's eligibility re-check: between dispatch and worker start
        the item can be assigned, closed, labeled, or worked by a human. The
        whole predicate is re-checked, not existence, so every exclusion
        mechanism counts — not just assignment. Read-only.
        """
        ...

    def claim(self, item: WorkItem) -> bool:
        """Take ownership of an item.

        True means this worker now owns it. False means somebody else does and
        the worker should exit `no_action_needed`.
        """
        ...

    def claim_prognosis(self, item: WorkItem) -> ClaimPrognosis:
        """Who holds the item now, and whether `claim` would take it.

        Read-only: this is what `tina run --dry-run` asks instead of claiming,
        so an implementation that writes anything has broken the contract.
        """
        ...

    def claimed(self, q: str) -> list[WorkItem]:
        """The items matching `q` that the bot currently holds.

        The complement of `query`, not a filter on top of it: a track query
        excludes claimed items by construction (ADR-004), so the adapter
        *replaces* that exclusion with its own identity rather than appending
        to it. Read-only, like `claim_prognosis` — one search, no writes.
        """
        ...

    def annotate(self, item: WorkItem, comment: str) -> None:
        """Leave a comment about a run on the item.

        A lifecycle write, not a result write (ADR-013). Best-effort: a
        failure is logged and swallowed, never raised — a reporting hiccup
        must not mask the failure it reports.
        """
        ...

    def block(self, item: WorkItem) -> None:
        """Apply the exclusion marker so the configured query stops matching.

        Idempotent — blocking an already-blocked item is a no-op — and
        best-effort, like `annotate`. The marker only works when the track
        query excludes it; Tina never rewrites queries.
        """
        ...


class SourceError(TinaError, RuntimeError):
    """A source adapter could not talk to its tracker."""


@dataclass(frozen=True)
class RetryRule:
    """One transient-failure shape, and the waits between re-issues.

    `waits` is the ladder: one retry per entry, so its length bounds the
    attempts. `marker` narrows the match to responses whose body carries the
    text — how a documented rate-limit message is told apart from an ordinary
    refusal with the same status. `retry_after` honors a numeric Retry-After
    header, with the ladder entry as the ceiling, so the server cannot demand
    an arbitrary sleep.
    """

    status: frozenset[int]
    waits: tuple[float, ...]
    marker: str = ""
    retry_after: bool = False

    def matches(self, response: httpx.Response) -> bool:
        if response.status_code not in self.status:
            return False
        return not self.marker or self.marker in response.text

    def wait(self, response: httpx.Response, attempt: int) -> float:
        wait = self.waits[attempt]
        if self.retry_after:
            header = response.headers.get("Retry-After", "")
            if header.isdigit():
                wait = min(float(header), wait)
        return wait


def send_with_retry(
    send: Callable[[], httpx.Response],
    rules: Sequence[RetryRule],
    sleep: Callable[[float], None],
    source: str,
    request: str,
) -> httpx.Response:
    """Issue a request, re-issuing transient failures per the rules.

    Transient tracker failures are intermittent and look like an empty
    backlog, which is the wrong thing to be ambiguous about. Each rule's
    ladder is spent independently; once no rule has a wait left, the response
    is returned as-is — the caller owns the error path, so a permanent
    failure still raises there with the original message.
    """
    spent = [0] * len(rules)
    while True:
        response = send()
        if response.status_code < 400:
            return response
        index = next(
            (
                i
                for i, rule in enumerate(rules)
                if spent[i] < len(rule.waits) and rule.matches(response)
            ),
            None,
        )
        if index is None:
            return response
        wait = rules[index].wait(response, spent[index])
        spent[index] += 1
        log.warning(
            "transient failure; retrying",
            extra={
                "source": source,
                "request": request,
                "status": response.status_code,
                "wait_seconds": wait,
            },
        )
        sleep(wait)


def require_env(name: str, source: str) -> str:
    """Read a required environment variable or fail with a usable message."""
    value = os.environ.get(name)
    if not value:
        raise SourceError(
            f"{source} source requires the {name} environment variable",
            fix=f"Set {name} in the worker environment.",
        )
    return value


def parse_payload[M: BaseModel](
    model: type[M], response: httpx.Response, source: str, path: str
) -> M:
    """Validate a tracker response against the shape Tina expects.

    The models only declare the fields Tina reads and tolerate unknown ones, so
    this fires when a tracker returns something genuinely different — not every
    time an API grows a field.
    """
    try:
        return model.model_validate(response.json())
    except ValueError as exc:
        detail = exc if isinstance(exc, ValidationError) else f"response was not JSON: {exc}"
        raise SourceError(f"{source}: unexpected response from {path}: {detail}") from None
