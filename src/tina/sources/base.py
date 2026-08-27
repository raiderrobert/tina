"""The source adapter contract."""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from tina.errors import TinaError
from tina.models import WorkItem


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
