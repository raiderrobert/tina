"""Jira source adapter.

Claiming is a real compare-and-set: assignment is conditioned on the assignee
being empty, then confirmed by re-reading the issue.
"""

from __future__ import annotations

from typing import Any

import httpx

from tina.models import WorkItem
from tina.sources.base import SourceError, require_env

SEARCH_PATH = "/rest/api/3/search/jql"
ISSUE_PATH = "/rest/api/3/issue"
FIELDS = ["summary", "description", "assignee", "status"]


class JiraSource:
    """Jira Cloud REST API v3."""

    name = "jira"

    def __init__(
        self,
        client: httpx.Client | None = None,
        base_url: str | None = None,
        bot_account_id: str | None = None,
    ) -> None:
        self.base_url = (base_url or require_env("JIRA_BASE_URL", "jira")).rstrip("/")
        self._bot_account_id = bot_account_id
        if client is None:
            email = require_env("JIRA_EMAIL", "jira")
            token = require_env("JIRA_API_TOKEN", "jira")
            client = httpx.Client(auth=(email, token), timeout=30.0)
        self.client = client

    @property
    def bot_account_id(self) -> str:
        if self._bot_account_id is None:
            self._bot_account_id = require_env("JIRA_BOT_ACCOUNT_ID", "jira")
        return self._bot_account_id

    def query(self, q: str) -> list[WorkItem]:
        response = self._request(
            "POST",
            SEARCH_PATH,
            json={"jql": q, "maxResults": 100, "fields": FIELDS},
        )
        issues = response.json().get("issues", [])
        return [self._to_item(issue) for issue in issues]

    def get(self, item_id: str) -> WorkItem:
        response = self._request(
            "GET", f"{ISSUE_PATH}/{item_id}", params={"fields": ",".join(FIELDS)}
        )
        return self._to_item(response.json())

    def claim(self, item: WorkItem) -> bool:
        """Compare-and-set on the assignee field.

        Refuse if anyone already holds the issue, assign to the bot, then re-read
        to confirm the write landed and was not overwritten.
        """
        current = self._raw_issue(item.id)
        if (current.get("fields") or {}).get("assignee"):
            return False

        self._request(
            "PUT",
            f"{ISSUE_PATH}/{item.id}/assignee",
            json={"accountId": self.bot_account_id},
        )

        confirmed = self._raw_issue(item.id)
        assignee = (confirmed.get("fields") or {}).get("assignee") or {}
        return assignee.get("accountId") == self.bot_account_id

    def _raw_issue(self, item_id: str) -> dict[str, Any]:
        response = self._request(
            "GET", f"{ISSUE_PATH}/{item_id}", params={"fields": ",".join(FIELDS)}
        )
        return response.json()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self.base_url}{path}"
        try:
            response = self.client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise SourceError(f"jira: {method} {path} failed: {exc}") from exc
        if response.status_code >= 400:
            raise SourceError(
                f"jira: {method} {path} returned {response.status_code}: {response.text[:400]}"
            )
        return response

    def _to_item(self, issue: dict[str, Any]) -> WorkItem:
        fields = issue.get("fields") or {}
        key = issue.get("key") or str(issue.get("id", ""))
        return WorkItem(
            id=key,
            source=self.name,
            title=fields.get("summary") or "",
            description=render_adf(fields.get("description")),
            url=f"{self.base_url}/browse/{key}",
            raw=issue,
        )


def render_adf(node: Any) -> str:
    """Flatten an Atlassian Document Format tree to plain text.

    v3 returns rich documents; the agent only needs the prose. Unknown node
    types are traversed rather than dropped.
    """
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(render_adf(child) for child in node)
    if not isinstance(node, dict):
        return str(node)

    node_type = node.get("type")
    if node_type == "text":
        return str(node.get("text", ""))
    if node_type == "hardBreak":
        return "\n"

    body = render_adf(node.get("content"))
    if node_type in {"paragraph", "heading", "listItem", "codeBlock", "blockquote"}:
        return body + "\n"
    return body
