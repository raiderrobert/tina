"""GitHub Issues source adapter.

GitHub's assign API is an idempotent add with no conditional, so claiming is
assign-then-reread: the bot must end up as the *sole* assignee. A small race
window remains, which is acceptable — duplicate workers are already the
tolerated failure mode (architecture §9).
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from pydantic import AnyHttpUrl, BaseModel, Field, model_validator

from tina.log import get_logger
from tina.models import WorkItem
from tina.sources.base import ClaimPrognosis, SourceError, parse_payload, require_env

log = get_logger(__name__)

API_BASE = "https://api.github.com"
SEARCH_PATH = "/search/issues"
ACCEPT = "application/vnd.github+json"

#: The qualifier a track query uses to exclude claimed issues.
NO_ASSIGNEE = "no:assignee"


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
    ) -> None:
        if not repo:
            raise SourceError('github source requires repo = "owner/name" on the track')
        self.repo = repo
        self.blocked_label = blocked_label
        self.claim_policy = claim_policy
        self.claim_label = claim_label
        self.api_base = (api_base or os.environ.get("GITHUB_API_URL") or API_BASE).rstrip("/")
        self._bot_login = bot_login or os.environ.get("GITHUB_BOT_LOGIN")
        if client is None:
            token = require_env("GITHUB_TOKEN", "github")
            client = httpx.Client(
                headers={"Authorization": f"Bearer {token}", "Accept": ACCEPT},
                timeout=30.0,
            )
        self.client = client

    @property
    def bot_login(self) -> str:
        """Who we are. Configured, or looked up once from the token."""
        if not self._bot_login:
            viewer = parse_payload(Viewer, self._request("GET", "/user"), "github", "/user")
            self._bot_login = viewer.login
            if not self._bot_login:
                raise SourceError("github: could not determine the bot login from GET /user")
        return self._bot_login

    def query(self, q: str) -> list[WorkItem]:
        params = SearchParams(q=q)
        response = self._request("GET", SEARCH_PATH, params=params.model_dump())
        result = parse_payload(SearchResult, response, "github", SEARCH_PATH)
        return [self._to_item(issue) for issue in result.items]

    def get(self, item_id: str) -> WorkItem:
        return self._to_item(self._issue(_number(item_id)))

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

    def claimed(self, q: str) -> list[WorkItem]:
        """The bot's own issues: the track query with its exclusion inverted.

        Which token gets inverted follows the claim policy — `no:assignee`
        under assign, the negated claim label under label. Routed through
        `query`, so this is the same single `GET /search/issues` a dispatch
        makes. Under `claim = "none"` the bot never holds anything, so the
        answer is an empty list, without a search that would imply otherwise.
        """
        if self.claim_policy == "none":
            return []
        if self.claim_policy == "label":
            return self.query(claimed_label_search(q, str(self.claim_label)))
        return self.query(claimed_search(q, self.bot_login))

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
            response = self.client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise SourceError(f"github: {method} {path} failed: {exc}") from exc
        if response.status_code >= 400:
            raise SourceError(
                f"github: {method} {path} returned {response.status_code}: {response.text[:400]}"
            )
        return response

    def _to_item(self, issue: Issue) -> WorkItem:
        return WorkItem(
            id=str(issue.number),
            source=self.name,
            title=issue.title,
            description=issue.body or "",
            url=issue.html_url,
            raw=issue.raw,
        )


def claimed_search(q: str, login: str) -> str:
    """Swap the `no:assignee` qualifier for the bot, other qualifiers unmoved.

    A whitespace-token scan rather than a regex: `\\bno:assignee\\b` also matches
    inside `label:"no:assignee"`, because the quote supplies the word boundary,
    and would rewrite a label into a qualifier. Exact token equality is the only
    rule that tells the qualifier apart from a literal containing its text.
    """
    tokens = q.split()
    if not any(token.lower() == NO_ASSIGNEE for token in tokens):
        raise SourceError(
            f"github: the track query has no {NO_ASSIGNEE} qualifier to invert: {q!r}",
            fix=f"Add `{NO_ASSIGNEE}` to the track query so dispatch skips claimed issues.",
        )
    return " ".join(
        f"assignee:{login}" if token.lower() == NO_ASSIGNEE else token for token in tokens
    )


def claimed_label_search(q: str, label: str) -> str:
    """Swap the negated claim-label token for its positive, other tokens unmoved.

    The same exact-token scan as `claimed_search`, for the same reason: only
    token equality tells the qualifier apart from a literal containing its
    text. The label value may be bare or quoted.
    """
    negated = {f"-label:{label.lower()}", f'-label:"{label.lower()}"'}
    tokens = q.split()
    if not any(token.lower() in negated for token in tokens):
        raise SourceError(
            f"github: the track query has no -label:{label} qualifier to invert: {q!r}",
            fix=f"Add `-label:{label}` to the track query so dispatch skips claimed issues.",
        )
    return " ".join(f"label:{label}" if token.lower() in negated else token for token in tokens)


def _number(item_id: str) -> str:
    """Accept `123`, `#123`, or `owner/name#123` — all mean issue 123."""
    return item_id.rsplit("#", 1)[-1].lstrip("#").strip()
