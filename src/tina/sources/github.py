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

from tina.models import WorkItem
from tina.sources.base import SourceError, parse_payload, require_env

API_BASE = "https://api.github.com"
SEARCH_PATH = "/search/issues"
ACCEPT = "application/vnd.github+json"


class SearchParams(BaseModel):
    """Query string for the issues search API."""

    q: str
    per_page: int = 100


class User(BaseModel):
    login: str = ""


class Issue(BaseModel):
    """A GitHub issue, narrowed to what Tina reads.

    `number` is required: an issue without one cannot be claimed or linked, so a
    payload missing it is a broken response rather than a sparse one.
    """

    number: int
    title: str = ""
    body: str | None = None
    html_url: AnyHttpUrl | None = None
    assignees: list[User] = Field(default_factory=list)
    #: The payload this was validated from, kept for `WorkItem.raw`.
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _keep_raw(cls, data: Any) -> Any:
        return {**data, "raw": data} if isinstance(data, dict) else data

    @property
    def logins(self) -> list[str]:
        return [user.login for user in self.assignees]


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
    ) -> None:
        if not repo:
            raise SourceError('github source requires repo = "owner/name" on the workflow')
        self.repo = repo
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
        """Add the bot as assignee, then confirm it is the only one."""
        number = _number(item.id)
        self._request(
            "POST",
            f"/repos/{self.repo}/issues/{number}/assignees",
            json={"assignees": [self.bot_login]},
        )
        return self._issue(number).logins == [self.bot_login]

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


def _number(item_id: str) -> str:
    """Accept `123`, `#123`, or `owner/name#123` — all mean issue 123."""
    return item_id.rsplit("#", 1)[-1].lstrip("#").strip()
