"""GitHub Issues source adapter.

GitHub's assign API is an idempotent add with no conditional, so claiming is
assign-then-reread: the bot must end up as the *sole* assignee. A small race
window remains, which is acceptable — duplicate workers are already the
tolerated failure mode (architecture §9).
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import AnyHttpUrl, BaseModel, Field, model_validator

from tina import credentials
from tina.log import get_logger
from tina.models import WorkItem
from tina.query import SOURCE_FEATURES, Assignee, Query
from tina.sources.base import (
    ClaimPrognosis,
    RetryRule,
    SourceError,
    parse_payload,
    require_env,
    send_with_retry,
)

log = get_logger(__name__)

API_BASE = "https://api.github.com"
SEARCH_PATH = "/search/issues"
ACCEPT = "application/vnd.github+json"

#: The query nodes this source compiles (`tina.query.SOURCE_FEATURES`).
FEATURES = SOURCE_FEATURES["github"]

#: `@me` is whoever the token belongs to — the bot — so the held-by query
#: needs no login lookup. Search has no OR over assignees, and a claim
#: transition is a Jira-only key, so `UNASSIGNED_OR_ME` never arrives here.
_ASSIGNEE = {Assignee.UNASSIGNED: "no:assignee", Assignee.ME: "assignee:@me"}

#: GitHub reports its secondary rate limit as a 403 whose body carries this
#: documented phrase. Back-to-back searches trip it easily.
SECONDARY_RATE_LIMIT = "secondary rate limit"

#: The secondary rate limit is retried once after the documented minimum wait
#: — never a hammer; the second refusal is loud. Gateway errors get a short
#: ladder. Every other 4xx is permanent and raises immediately.
RETRY_RULES = (
    RetryRule(status=frozenset({403}), waits=(60.0,), marker=SECONDARY_RATE_LIMIT),
    RetryRule(status=frozenset({502, 503, 504}), waits=(2.0, 8.0)),
)


class SearchParams(BaseModel):
    """Query string for the issues search API."""

    q: str
    per_page: int = 100


class User(BaseModel):
    login: str = ""


class Label(BaseModel):
    name: str = ""


class Issue(BaseModel):
    """A GitHub issue, narrowed to what Tina reads.

    `number` is required: an issue without one cannot be claimed or linked, so a
    payload missing it is a broken response rather than a sparse one.
    """

    number: int
    title: str = ""
    body: str | None = None
    state: str = ""
    html_url: AnyHttpUrl | None = None
    assignees: list[User] = Field(default_factory=list)
    labels: list[Label] = Field(default_factory=list)
    #: The payload this was validated from, kept for `WorkItem.raw`.
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _keep_raw(cls, data: Any) -> Any:
        return {**data, "raw": data} if isinstance(data, dict) else data

    @property
    def logins(self) -> list[str]:
        return [user.login for user in self.assignees]

    @property
    def label_names(self) -> list[str]:
        return [label.name for label in self.labels]


class SearchResult(BaseModel):
    items: list[Issue] = Field(default_factory=list)


class Viewer(BaseModel):
    """`GET /user` — who the token belongs to."""

    login: str = ""


class GitHubSource:
    """GitHub Issues via the REST API."""

    name = "github"

    def __init__(
        self,
        repo: str,
        client: httpx.Client | None = None,
        bot_login: str | None = None,
        api_base: str | None = None,
        blocked_label: str = "tina-blocked",
        claim_policy: str = "assign",
        claim_label: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not repo:
            raise SourceError('github source requires repo = "owner/name" on the track')
        self.repo = repo
        self._sleep = sleep
        self.blocked_label = blocked_label
        self.claim_policy = claim_policy
        self.claim_label = claim_label
        self.api_base = (api_base or os.environ.get("GITHUB_API_URL") or API_BASE).rstrip("/")
        self._bot_login = bot_login or os.environ.get("GITHUB_BOT_LOGIN")
        # A deployment minting short-lived tokens names the command that
        # produces one; it is re-run on a 401 and the request retried once.
        self._token_command = credentials.token_command(credentials.GITHUB_TOKEN_COMMAND)
        if client is None:
            token = self._fresh_token() or require_env("GITHUB_TOKEN", "github", "GH_TOKEN")
            client = httpx.Client(
                headers={"Authorization": f"Bearer {token}", "Accept": ACCEPT},
                timeout=30.0,
            )
        self.client = client

    def _fresh_token(self) -> str | None:
        """Run the token command, when there is one."""
        if self._token_command is None:
            return None
        return credentials.run_token_command(self._token_command, credentials.GITHUB_TOKEN_COMMAND)

    @property
    def bot_login(self) -> str:
        """Who we are. Configured, or looked up once from the token."""
        if not self._bot_login:
            viewer = parse_payload(Viewer, self._request("GET", "/user"), "github", "/user")
            self._bot_login = viewer.login
            if not self._bot_login:
                raise SourceError("github: could not determine the bot login from GET /user")
        return self._bot_login

    def login(self) -> str:
        """`GET /user`: the token works, and this is who it acts as."""
        return self.bot_login

    def query(self, q: Query) -> list[WorkItem]:
        params = SearchParams(q=compile(q))
        response = self._request("GET", SEARCH_PATH, params=params.model_dump())
        result = parse_payload(SearchResult, response, "github", SEARCH_PATH)
        return [self._to_item(issue) for issue in result.items]

    def get(self, item_id: str) -> WorkItem:
        return self._to_item(self._issue(_number(item_id)))

    def matches(self, item_id: str, q: Query) -> bool:
        """Fetch the issue and evaluate the query against it in code.

        Search has no number qualifier, so the predicate is checked locally
        (`satisfies`). The query is a tree, so every node is checked — there
        is no unstructured remainder to wave through.
        """
        return satisfies(self._issue(_number(item_id)), q)

    def claim(self, item: WorkItem) -> bool:
        """Take the item under the track's claim policy (ADR-014).

        Assign adds the bot as assignee, then confirms it is the only one. A
        label claim adds `claim_label`, then confirms it stuck — refusing up
        front when the label is already present, since a label carries no
        identity and present always means someone else holds it.
        """
        if self.claim_policy == "label":
            return self._claim_by_label(item)
        number = _number(item.id)
        self._request(
            "POST",
            f"/repos/{self.repo}/issues/{number}/assignees",
            json={"assignees": [self.bot_login]},
        )
        return self._issue(number).logins == [self.bot_login]

    def _claim_by_label(self, item: WorkItem) -> bool:
        number = _number(item.id)
        if self.claim_label in self._issue(number).label_names:
            return False

        self._request(
            "POST",
            f"/repos/{self.repo}/issues/{number}/labels",
            json={"labels": [self.claim_label]},
        )

        return self.claim_label in self._issue(number).label_names

    def claim_prognosis(self, item: WorkItem) -> ClaimPrognosis:
        """The re-read half of `claim`, with the write that precedes it left off.

        Assignment is an idempotent add, so the bot already holding the issue
        alone is a claim that would succeed — the opposite of Jira, where any
        other assignee refuses. Under label, a present claim label refuses,
        whoever put it there. `bot_login` may cost a `GET /user`; still no
        write.
        """
        issue = self._issue(_number(item.id))
        if self.claim_policy == "label":
            if self.claim_label in issue.label_names:
                return ClaimPrognosis(would_claim=False, holder=f"label:{self.claim_label}")
            return ClaimPrognosis(would_claim=True, holder="")
        logins = issue.logins
        if not logins:
            return ClaimPrognosis(would_claim=True, holder="")
        if logins == [self.bot_login]:
            return ClaimPrognosis(would_claim=True, holder=self.bot_login)
        return ClaimPrognosis(would_claim=False, holder=", ".join(logins))

    def claimed(self, q: Query) -> list[WorkItem]:
        """The bot's own issues: the track query with its exclusion inverted.

        Which node gets inverted follows the claim policy — the assignee
        under assign, the claim label under label (`Query.held_by`). Routed
        through `query`, so this is the same single `GET /search/issues` a
        dispatch makes. Under `claim = "none"` the bot never holds anything,
        so the answer is an empty list, without a search that would imply
        otherwise.
        """
        if self.claim_policy == "none":
            return []
        return self.query(q.held_by(self.claim_policy, self.claim_label))

    def annotate(self, item: WorkItem, comment: str) -> None:
        """Comment on the issue. Best-effort per the contract: log, never raise."""
        try:
            self._request(
                "POST",
                f"/repos/{self.repo}/issues/{_number(item.id)}/comments",
                json={"body": comment},
            )
        except SourceError as exc:
            log.warning("annotate failed", extra={"item": item.id, "error": str(exc)})
            return
        log.info("item annotated", extra={"item": item.id})

    def block(self, item: WorkItem) -> None:
        """Add the exclusion label, `tina-blocked` unless the track overrides it.

        GitHub's label add returns the issue's full label set, so adding one
        it already carries is a no-op rather than an error. Best-effort, like
        `annotate`.
        """
        try:
            self._request(
                "POST",
                f"/repos/{self.repo}/issues/{_number(item.id)}/labels",
                json={"labels": [self.blocked_label]},
            )
        except SourceError as exc:
            log.warning("block failed", extra={"item": item.id, "error": str(exc)})
            return
        log.info("item blocked", extra={"item": item.id, "label": self.blocked_label})

    def _issue(self, number: str) -> Issue:
        path = f"/repos/{self.repo}/issues/{number}"
        return parse_payload(Issue, self._request("GET", path), "github", path)

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self.api_base}{path}"
        try:
            response = send_with_retry(
                lambda: self.client.request(method, url, **kwargs),
                RETRY_RULES,
                self._sleep,
                "github",
                f"{method} {path}",
            )
            if response.status_code == 401 and self._token_command is not None:
                # The token aged out mid-run. Mint again, retry exactly once; a
                # second 401 is a real refusal and raises like any other.
                self.client.headers["Authorization"] = f"Bearer {self._fresh_token()}"
                response = self.client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise SourceError(f"github: {method} {path} failed: {exc}") from exc
        if response.status_code >= 400:
            raise SourceError(
                f"github: {method} {path} returned {response.status_code}: {response.text[:400]}"
            )
        return response

    def _to_item(self, issue: Issue) -> WorkItem:
        """`owner/name#N`: an issue's identity is its repo and its number.

        Fully qualified so the id is unambiguous in a run record or a log line,
        and so a skill can name the repo without parsing the payload. Every
        entry point accepts the bare number too (`_number`).
        """
        return WorkItem(
            id=f"{self.repo}#{issue.number}",
            source=self.name,
            title=issue.title,
            description=issue.body or "",
            url=issue.html_url,
            raw=issue.raw,
        )


def compile(q: Query) -> str:
    """Issue search for a query: one qualifier per node.

    Every required label is its own `label:` qualifier (GitHub ANDs them);
    excluded labels are negated. Values are quoted so labels with spaces
    survive tokenization. Search has no item qualifier, so a scoped query
    cannot be compiled — `matches` evaluates it locally instead.
    """
    unsupported = q.features() - FEATURES
    if unsupported:
        raise SourceError(f"github: cannot compile {', '.join(sorted(unsupported))}")
    if q.item is not None:
        raise SourceError("github: search has no item qualifier; evaluate with `satisfies`")
    if q.assignee not in _ASSIGNEE:
        raise SourceError(f"github: search cannot express assignee = {q.assignee}")
    parts = [f"repo:{q.scope}", "is:issue", "is:open", _ASSIGNEE[q.assignee]]
    parts += [f'label:"{label}"' for label in q.labels_all]
    parts += [f'-label:"{label}"' for label in q.labels_none]
    return " ".join(parts)


def satisfies(issue: Issue, q: Query) -> bool:
    """Whether a fetched issue matches the query, node by node.

    The local counterpart of `compile`, for the one question search cannot
    answer: does this issue, by number, still match? `scope` holds by
    construction — the issue was fetched from the repo. Label comparison is
    case-insensitive, as GitHub's is.
    """
    if issue.state != "open":
        return False
    if q.assignee is Assignee.UNASSIGNED and issue.assignees:
        return False
    labels = {name.lower() for name in issue.label_names}
    if any(label.lower() not in labels for label in q.labels_all):
        return False
    return not any(label.lower() in labels for label in q.labels_none)


def _number(item_id: str) -> str:
    """Accept `123`, `#123`, or `owner/name#123` — all mean issue 123."""
    return item_id.rsplit("#", 1)[-1].lstrip("#").strip()
