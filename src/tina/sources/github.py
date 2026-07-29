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

from tina.models import WorkItem
from tina.sources.base import SourceError, require_env

API_BASE = "https://api.github.com"
ACCEPT = "application/vnd.github+json"


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
            self._bot_login = self._request("GET", "/user").json().get("login", "")
            if not self._bot_login:
                raise SourceError("github: could not determine the bot login from GET /user")
        return self._bot_login

    def query(self, q: str) -> list[WorkItem]:
        response = self._request("GET", "/search/issues", params={"q": q, "per_page": 100})
        return [self._to_item(issue) for issue in response.json().get("items", [])]

    def get(self, item_id: str) -> WorkItem:
        response = self._request("GET", f"/repos/{self.repo}/issues/{_number(item_id)}")
        return self._to_item(response.json())

    def claim(self, item: WorkItem) -> bool:
        """Add the bot as assignee, then confirm it is the only one."""
        number = _number(item.id)
        self._request(
            "POST",
            f"/repos/{self.repo}/issues/{number}/assignees",
            json={"assignees": [self.bot_login]},
        )
        confirmed = self._request("GET", f"/repos/{self.repo}/issues/{number}").json()
        logins = [a.get("login") for a in confirmed.get("assignees") or []]
        return logins == [self.bot_login]

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

    def _to_item(self, issue: dict[str, Any]) -> WorkItem:
        return WorkItem(
            id=str(issue.get("number", "")),
            source=self.name,
            title=issue.get("title") or "",
            description=issue.get("body") or "",
            url=issue.get("html_url") or "",
            raw=issue,
        )


def _number(item_id: str) -> str:
    """Accept `123`, `#123`, or `owner/name#123` — all mean issue 123."""
    return item_id.rsplit("#", 1)[-1].lstrip("#").strip()
