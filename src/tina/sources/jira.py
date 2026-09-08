"""Jira source adapter.

Claiming is a real compare-and-set: assignment is conditioned on the assignee
being empty, then confirmed by re-reading the issue.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import BaseModel, Field, model_validator

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

SEARCH_PATH = "/rest/api/3/search/jql"
ISSUE_PATH = "/rest/api/3/issue"
MYSELF_PATH = "/rest/api/3/myself"
FIELDS = ["summary", "description", "assignee", "status", "labels"]

#: Server errors get a short ladder. A rate limit is retried once, honoring
#: Retry-After up to the minute the ladder allows. Every other 4xx is
#: permanent and raises immediately.
RETRY_RULES = (
    RetryRule(status=frozenset({500, 502, 503, 504}), waits=(2.0, 8.0)),
    RetryRule(status=frozenset({429}), waits=(60.0,), retry_after=True),
)

#: The query nodes this source compiles (`tina.query.SOURCE_FEATURES`).
FEATURES = SOURCE_FEATURES["jira"]

#: The status a track reads from when it sets none. A workflow label the
#: tracker owns, not Tina, so it is a config knob rather than a constant.
DEFAULT_STATUS = "Open"

#: `currentUser()` is whoever the credentials belong to — the bot — so the
#: held-by query needs no account lookup.
_ASSIGNEE = {
    Assignee.UNASSIGNED: "assignee IS EMPTY",
    Assignee.UNASSIGNED_OR_ME: "(assignee IS EMPTY OR assignee = currentUser())",
    Assignee.ME: "assignee = currentUser()",
}


class SearchRequest(BaseModel):
    """The JQL search body. Serialized with Jira's camelCase field names."""

    jql: str
    max_results: int = Field(default=100, serialization_alias="maxResults")
    fields: list[str] = Field(default=FIELDS)


class User(BaseModel):
    """Only the one field claiming compares on."""

    account_id: str | None = Field(default=None, alias="accountId")


class Status(BaseModel):
    name: str = ""


class IssueFields(BaseModel):
    summary: str = ""
    # Atlassian Document Format: a tree Tina flattens rather than models.
    description: Any = None
    assignee: User | None = None
    labels: list[str] = Field(default_factory=list)
    status: Status | None = None


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


class Transition(BaseModel):
    """One available workflow transition. `to` is the status it lands in —
    what a track names, since transition names and status names differ."""

    id: str = ""
    name: str = ""
    to: Status | None = None

    def reaches(self, status: str) -> bool:
        wanted = status.lower()
        return self.name.lower() == wanted or (
            self.to is not None and self.to.name.lower() == wanted
        )


class Myself(BaseModel):
    """`GET /rest/api/3/myself` — who the credentials belong to."""

    account_id: str = Field(default="", alias="accountId")
    display_name: str = Field(default="", alias="displayName")


class TransitionList(BaseModel):
    transitions: list[Transition] = Field(default_factory=list)


class JiraSource:
    """Jira Cloud REST API v3."""

    name = "jira"

    def __init__(
        self,
        client: httpx.Client | None = None,
        base_url: str | None = None,
        bot_account_id: str | None = None,
        blocked_label: str = "tina-blocked",
        claim_policy: str = "assign",
        claim_label: str | None = None,
        claim_transition: str | None = None,
        blocked_transition: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = (base_url or require_env("JIRA_BASE_URL", "jira")).rstrip("/")
        self._sleep = sleep
        self._bot_account_id = bot_account_id or os.environ.get("JIRA_BOT_ACCOUNT_ID")
        self.blocked_label = blocked_label
        self.claim_policy = claim_policy
        self.claim_label = claim_label
        self.claim_transition = claim_transition
        self.blocked_transition = blocked_transition
        if client is None:
            email = require_env("JIRA_EMAIL", "jira")
            token = require_env("JIRA_API_TOKEN", "jira")
            client = httpx.Client(auth=(email, token), timeout=30.0)
        self.client = client

    @property
    def bot_account_id(self) -> str:
        """Who we are. Configured, or looked up once from the credentials."""
        if not self._bot_account_id:
            me = parse_payload(Myself, self._request("GET", MYSELF_PATH), "jira", MYSELF_PATH)
            self._bot_account_id = me.account_id
            if not self._bot_account_id:
                raise SourceError(f"jira: could not determine the bot account from {MYSELF_PATH}")
        return self._bot_account_id

    def login(self) -> str:
        """`GET /myself`: the credentials work, and this is who they act as."""
        return self.bot_account_id

    def query(self, q: Query) -> list[WorkItem]:
        request = SearchRequest(jql=compile(q))
        response = self._request("POST", SEARCH_PATH, json=request.model_dump(by_alias=True))
        result = parse_payload(SearchResult, response, "jira", SEARCH_PATH)
        return [self._to_item(issue) for issue in result.issues]

    def get(self, item_id: str) -> WorkItem:
        return self._to_item(self._issue(item_id))

    def matches(self, item_id: str, q: Query) -> bool:
        """One search: the configured query scoped to the one item.

        The tracker evaluates the whole predicate, so whatever mechanism
        excluded the item — assignment, status, a label — is caught here.
        """
        return bool(self.query(q.scoped_to(item_id)))

    def claim(self, item: WorkItem) -> bool:
        """Take the item under the track's claim policy (ADR-014).

        Assign is a compare-and-set on the assignee field: refuse anyone else,
        assign to the bot, re-read to confirm the write landed. An issue the
        bot already holds re-claims successfully. A label claim is the same
        shape with the claim label in place of the assignee — except that a
        label carries no identity, so present always refuses. Either way, a
        confirmed claim then applies `claim_transition` when the track set one.
        """
        taken = self._claim_by_label(item) if self.claim_policy == "label" else self._assign(item)
        if taken and self.claim_transition:
            self._apply_transition(item)
        return taken

    def _assign(self, item: WorkItem) -> bool:
        assignee = self._issue(item.id).fields.assignee
        if assignee is not None:
            return assignee.account_id == self.bot_account_id

        self._request(
            "PUT",
            f"{ISSUE_PATH}/{item.id}/assignee",
            json={"accountId": self.bot_account_id},
        )

        assignee = self._issue(item.id).fields.assignee
        return assignee is not None and assignee.account_id == self.bot_account_id

    def _claim_by_label(self, item: WorkItem) -> bool:
        if self.claim_label in self._issue(item.id).fields.labels:
            return False

        self._request(
            "PUT",
            f"{ISSUE_PATH}/{item.id}",
            json={"update": {"labels": [{"add": self.claim_label}]}},
        )

        return self.claim_label in self._issue(item.id).fields.labels

    def _apply_transition(self, item: WorkItem) -> None:
        """Move the claimed issue out of the queued status. Best-effort, like
        the other lifecycle writes: the claim already stands, and a stale
        status is the lesser bug than an assigned item left unworked."""
        self._transition(item, str(self.claim_transition), "claim transition")

    def _transition(self, item: WorkItem, target: str, what: str) -> None:
        """Apply the transition named `target`, or the one landing in the
        status named `target` — a track may spell either. Best-effort."""
        path = f"{ISSUE_PATH}/{item.id}/transitions"
        try:
            response = self._request("GET", path)
            available = parse_payload(TransitionList, response, "jira", path).transitions
            match = next((t for t in available if t.reaches(target)), None)
            if match is None:
                log.warning(f"{what} not available", extra={"item": item.id, "transition": target})
                return
            self._request("POST", path, json={"transition": {"id": match.id}})
        except SourceError as exc:
            log.warning(f"{what} failed", extra={"item": item.id, "error": str(exc)})
            return
        log.info("item transitioned", extra={"item": item.id, "transition": target})

    def claim_prognosis(self, item: WorkItem) -> ClaimPrognosis:
        """The `GET` half of `claim`, with the write that follows it left off.

        Consistent with each strategy: under assign the bot already holding
        the issue is a claim that would proceed; under label a present claim
        label refuses, whoever put it there.
        """
        if self.claim_policy == "label":
            if self.claim_label in self._issue(item.id).fields.labels:
                return ClaimPrognosis(would_claim=False, holder=f"label:{self.claim_label}")
            return ClaimPrognosis(would_claim=True, holder="")
        assignee = self._issue(item.id).fields.assignee
        if assignee is None:
            return ClaimPrognosis(would_claim=True, holder="")
        if assignee.account_id == self.bot_account_id:
            return ClaimPrognosis(would_claim=True, holder=self.bot_account_id)
        # An assignee with no accountId still holds the issue, and `holder=""`
        # is reserved for nobody holding it.
        return ClaimPrognosis(would_claim=False, holder=assignee.account_id or "unknown")

    def claimed(self, q: Query) -> list[WorkItem]:
        """The bot's own issues: the track query with its exclusion inverted.

        Which node gets inverted follows the claim policy — the assignee
        under assign, the claim label under label (`Query.held_by`). Routed
        through `query`, so this is the same single `POST /rest/api/3/search/jql`
        a dispatch makes. Under `claim = "none"` the bot never holds anything,
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
                f"{ISSUE_PATH}/{item.id}/comment",
                json={"body": adf_document(comment)},
            )
        except SourceError as exc:
            log.warning("annotate failed", extra={"item": item.id, "error": str(exc)})
            return
        log.info("item annotated", extra={"item": item.id})

    def block(self, item: WorkItem) -> None:
        """Move the item where the query stops matching it.

        With `blocked_transition` set, that is a status transition — the
        query's own `status =` clause excludes it, and humans find the item
        in the workflow state they already watch. Otherwise the exclusion
        label, `tina-blocked` unless the track overrides it: Jira's label add
        is a set add, so an already-blocked issue is a no-op rather than an
        error. Best-effort either way, like `annotate`.
        """
        if self.blocked_transition:
            self._transition(item, self.blocked_transition, "block transition")
            return
        try:
            self._request(
                "PUT",
                f"{ISSUE_PATH}/{item.id}",
                json={"update": {"labels": [{"add": self.blocked_label}]}},
            )
        except SourceError as exc:
            log.warning("block failed", extra={"item": item.id, "error": str(exc)})
            return
        log.info("item blocked", extra={"item": item.id, "label": self.blocked_label})

    def _issue(self, item_id: str) -> Issue:
        path = f"{ISSUE_PATH}/{item_id}"
        response = self._request("GET", path, params={"fields": ",".join(FIELDS)})
        return parse_payload(Issue, response, "jira", path)

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self.base_url}{path}"
        try:
            response = send_with_retry(
                lambda: self.client.request(method, url, **kwargs),
                RETRY_RULES,
                self._sleep,
                "jira",
                f"{method} {path}",
            )
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


def compile(q: Query) -> str:
    """JQL for a query: one `AND` clause per node, oldest first.

    `fields` becomes one `"Field" in ("a", "b")` clause per field — the field
    name is quoted, so custom fields with spaces work. Excluded labels carry
    the `labels IS EMPTY OR` guard, because JQL's `not in` does not match
    issues that have no labels at all. A scoped query drops the sort: one
    key needs no order.
    """
    unsupported = q.features() - FEATURES
    if unsupported:
        raise SourceError(f"jira: cannot compile {', '.join(sorted(unsupported))}")
    clauses = [f"project = {q.scope}", f'status = "{q.status or DEFAULT_STATUS}"']
    for field, values in q.fields.items():
        clauses.append(f'"{field}" in ({_quoted(values)})')
    clauses.append(_ASSIGNEE[q.assignee])
    clauses += [f'labels = "{label}"' for label in q.labels_all]
    if q.labels_none:
        clauses.append(f"(labels IS EMPTY OR labels not in ({_quoted(q.labels_none)}))")
    if q.extra:
        clauses.append(f"({q.extra})")
    if q.item:
        clauses.append(f'key = "{q.item}"')
        return " AND ".join(clauses)
    return " AND ".join(clauses) + " ORDER BY created ASC"


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f'"{value}"' for value in values)


def adf_document(text: str) -> dict[str, Any]:
    """Wrap plain text in the minimal Atlassian Document Format envelope.

    One paragraph per line — the write-side counterpart of `render_adf`. An
    empty line becomes an empty paragraph, because a text node with empty
    text is invalid ADF.
    """
    lines = text.splitlines() or [""]
    return {"type": "doc", "version": 1, "content": [_paragraph(line) for line in lines]}


def _paragraph(line: str) -> dict[str, Any]:
    if not line:
        return {"type": "paragraph"}
    return {"type": "paragraph", "content": [{"type": "text", "text": line}]}


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
