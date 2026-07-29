"""Jira source adapter.

Claiming is a real compare-and-set: assignment is conditioned on the assignee
being empty, then confirmed by re-reading the issue.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
from pydantic import BaseModel, Field, model_validator

from tina.models import WorkItem
from tina.sources.base import ClaimPrognosis, SourceError, parse_payload, require_env

SEARCH_PATH = "/rest/api/3/search/jql"
ISSUE_PATH = "/rest/api/3/issue"
FIELDS = ["summary", "description", "assignee", "status"]

#: The four spellings of "nobody holds this" that JQL accepts. `IS NOT EMPTY`
#: deliberately does not match: `NOT` is neither `EMPTY` nor `NULL`, so the
#: alternation fails and the query falls through to the error in `claimed_jql`
#: — it means the opposite, and silently inverting it would report the wrong
#: number.
EMPTY_ASSIGNEE = re.compile(r"\bassignee\s*(?:=|\bIS\b)\s*(?:EMPTY|NULL)\b", re.IGNORECASE)


class SearchRequest(BaseModel):
    """The JQL search body. Serialized with Jira's camelCase field names."""

    jql: str
    max_results: int = Field(default=100, serialization_alias="maxResults")
    fields: list[str] = Field(default=FIELDS)


class User(BaseModel):
    """Only the one field claiming compares on."""

    account_id: str | None = Field(default=None, alias="accountId")


class IssueFields(BaseModel):
    summary: str = ""
    # Atlassian Document Format: a tree Tina flattens rather than models.
    description: Any = None
    assignee: User | None = None


class Issue(BaseModel):
    """A Jira issue, narrowed to what Tina reads. Unknown fields pass through."""

    key: str = ""
    id: str = ""
    fields: IssueFields = Field(default_factory=IssueFields)
    #: The payload this was validated from, kept for `WorkItem.raw`.
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _keep_raw(cls, data: Any) -> Any:
        return {**data, "raw": data} if isinstance(data, dict) else data

    @property
    def identifier(self) -> str:
        return self.key or self.id


class SearchResult(BaseModel):
    issues: list[Issue] = Field(default_factory=list)


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
        request = SearchRequest(jql=q)
        response = self._request("POST", SEARCH_PATH, json=request.model_dump(by_alias=True))
        result = parse_payload(SearchResult, response, "jira", SEARCH_PATH)
        return [self._to_item(issue) for issue in result.issues]

    def get(self, item_id: str) -> WorkItem:
        return self._to_item(self._issue(item_id))

    def claim(self, item: WorkItem) -> bool:
        """Compare-and-set on the assignee field.

        Refuse if anyone already holds the issue, assign to the bot, then re-read
        to confirm the write landed and was not overwritten.
        """
        if self._issue(item.id).fields.assignee is not None:
            return False

        self._request(
            "PUT",
            f"{ISSUE_PATH}/{item.id}/assignee",
            json={"accountId": self.bot_account_id},
        )

        assignee = self._issue(item.id).fields.assignee
        return assignee is not None and assignee.account_id == self.bot_account_id

    def claim_prognosis(self, item: WorkItem) -> ClaimPrognosis:
        """The `GET` half of `claim`, with the `PUT` that follows it left off.

        Jira's compare-and-set refuses *any* existing assignee — the bot
        included — so anyone in the field is a claim that would not proceed.
        """
        assignee = self._issue(item.id).fields.assignee
        if assignee is None:
            return ClaimPrognosis(would_claim=True, holder="")
        # An assignee with no accountId still holds the issue, and `holder=""`
        # is reserved for nobody holding it.
        return ClaimPrognosis(would_claim=False, holder=assignee.account_id or "unknown")

    def claimed(self, q: str) -> list[WorkItem]:
        """The bot's own issues: the track query with its emptiness clause inverted.

        Routed through `query`, so this is the same single
        `POST /rest/api/3/search/jql` a dispatch makes — a different JQL string,
        not a different kind of request.
        """
        return self.query(claimed_jql(q, self.bot_account_id))

    def _issue(self, item_id: str) -> Issue:
        path = f"{ISSUE_PATH}/{item_id}"
        response = self._request("GET", path, params={"fields": ",".join(FIELDS)})
        return parse_payload(Issue, response, "jira", path)

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

    def _to_item(self, issue: Issue) -> WorkItem:
        key = issue.identifier
        return WorkItem(
            id=key,
            source=self.name,
            title=issue.fields.summary,
            description=render_adf(issue.fields.description),
            url=f"{self.base_url}/browse/{key}",
            raw=issue.raw,
        )


def claimed_jql(q: str, account_id: str) -> str:
    """Swap the empty-assignee clause for the bot, leaving the rest of `q` alone.

    A predicate is substituted for a predicate, so the surrounding `AND`s are
    preserved by construction and no clause can be left dangling — that is what
    keeps the two counts two halves of one question: project, status, and every
    other filter still apply to the in-flight count.

    Known limitation, accepted: a quoted literal containing the phrase —
    `summary ~ "assignee is empty"` — is rewritten too. JQL has no cheap way to
    skip string literals without a tokenizer, and a track whose text search
    contains that exact phrase is not a case worth a parser.
    """
    rewritten, swapped = EMPTY_ASSIGNEE.subn(f'assignee = "{account_id}"', q)
    if not swapped:
        raise SourceError(
            f"jira: the track query has no empty-assignee clause to invert: {q!r}",
            fix="Add `AND assignee IS EMPTY` to the track query so dispatch skips claimed issues.",
        )
    return rewritten


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
