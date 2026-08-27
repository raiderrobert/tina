from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest

from tina.models import WorkItem
from tina.sources.base import SourceError
from tina.sources.jira import SEARCH_PATH, JiraSource, render_adf

BASE = "https://acme.atlassian.net"
BOT = "bot-account-id"


def issue(key: str = "VUL-1", assignee: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "key": key,
        "fields": {
            "summary": "CVE-2024-0001 in libfoo",
            "description": {
                "type": "doc",
                "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "Bump libfoo."}]}
                ],
            },
            "assignee": assignee,
        },
    }


def source(handler, **kwargs: Any) -> JiraSource:
    return JiraSource(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        base_url=BASE,
        bot_account_id=BOT,
        **kwargs,
    )


def test_query_returns_normalized_items() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.content
        return httpx.Response(200, json={"issues": [issue(), issue("VUL-2")]})

    items = source(handler).query("project = VUL")

    assert seen["path"] == "/rest/api/3/search/jql"
    assert b"project = VUL" in seen["body"]
    assert [item.id for item in items] == ["VUL-1", "VUL-2"]
    assert items[0].title == "CVE-2024-0001 in libfoo"
    assert items[0].description.strip() == "Bump libfoo."
    assert str(items[0].url) == f"{BASE}/browse/VUL-1"
    assert items[0].raw["key"] == "VUL-1"


def test_get_fetches_one_issue() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/api/3/issue/VUL-1"
        return httpx.Response(200, json=issue())

    assert source(handler).get("VUL-1").id == "VUL-1"


def test_claim_refuses_an_already_assigned_issue(work_item: WorkItem) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(200, json=issue(assignee={"accountId": "someone-else"}))

    assert source(handler).claim(work_item) is False
    assert calls == ["GET"], "an assigned issue is never written to"


def test_claim_assigns_then_confirms(work_item: WorkItem) -> None:
    state: dict[str, Any] = {"assignee": None}
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "PUT":
            state["assignee"] = {"accountId": BOT}
            return httpx.Response(204)
        return httpx.Response(200, json=issue(assignee=state["assignee"]))

    assert source(handler).claim(work_item) is True
    assert [method for method, _ in calls] == ["GET", "PUT", "GET"]
    assert calls[1][1] == "/rest/api/3/issue/VUL-1/assignee"


def test_claim_fails_when_the_write_did_not_stick(work_item: WorkItem) -> None:
    """Someone raced us between the PUT and the re-read."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(204)
        return httpx.Response(200, json=issue(assignee=None))

    assert source(handler).claim(work_item) is False


def test_claim_prognosis_reports_the_holder_without_writing(work_item: WorkItem) -> None:
    """The verdict comes from a read. A write here would be the bug it exists to avoid."""
    calls: list[str] = []

    def held(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        assert request.method == "GET", "claim_prognosis must never write"
        return httpx.Response(200, json=issue(assignee={"accountId": "someone-else"}))

    def free(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        assert request.method == "GET", "claim_prognosis must never write"
        return httpx.Response(200, json=issue(assignee=None))

    taken = source(held).claim_prognosis(work_item)
    unheld = source(free).claim_prognosis(work_item)

    assert (taken.would_claim, taken.holder) == (False, "someone-else")
    assert (unheld.would_claim, unheld.holder) == (True, "")
    assert calls == ["GET", "GET"], "one read each, and nothing else"


def test_http_error_is_a_source_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    with pytest.raises(SourceError, match="403"):
        source(handler).query("project = VUL")


def test_missing_env_is_named() -> None:
    with pytest.raises(SourceError, match="JIRA_BASE_URL"):
        JiraSource(client=httpx.Client())


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        (None, ""),
        ("plain string", "plain string"),
        ({"type": "text", "text": "hi"}, "hi"),
        (
            {
                "type": "doc",
                "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "one"}]},
                    {"type": "paragraph", "content": [{"type": "text", "text": "two"}]},
                ],
            },
            "one\ntwo\n",
        ),
    ],
)
def test_render_adf(document: Any, expected: str) -> None:
    assert render_adf(document) == expected


def test_a_response_of_the_wrong_shape_is_a_source_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"issues": "not a list"})

    with pytest.raises(SourceError, match="unexpected response"):
        source(handler).query("project = VUL")


def test_non_json_is_a_source_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>maintenance</html>")

    with pytest.raises(SourceError, match="unexpected response"):
        source(handler).get("VUL-1")


def test_unknown_fields_are_tolerated() -> None:
    """Trackers grow fields; that is not a reason to stop working."""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = issue()
        payload["fields"]["someNewField"] = {"anything": True}
        payload["expand"] = "renderedFields"
        return httpx.Response(200, json=payload)

    item = source(handler).get("VUL-1")

    assert item.id == "VUL-1"
    assert item.raw["fields"]["someNewField"] == {"anything": True}, "raw keeps everything"


def test_claimed_swaps_the_empty_assignee_clause() -> None:
    """Every spelling of emptiness, with the rest of the query preserved."""
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content)["jql"])
        return httpx.Response(200, json={"issues": [issue(), issue("VUL-2")]})

    cases = [
        # the architecture doc's example: the other clauses survive untouched
        (
            "project = VUL AND status = Open AND assignee IS EMPTY",
            f'project = VUL AND status = Open AND assignee = "{BOT}"',
        ),
        # mid-query, and lowercase
        (
            "project = VUL AND assignee is empty AND status = Open",
            f'project = VUL AND assignee = "{BOT}" AND status = Open',
        ),
        ("assignee IS EMPTY", f'assignee = "{BOT}"'),
        ("assignee IS NULL", f'assignee = "{BOT}"'),
        ("assignee = EMPTY", f'assignee = "{BOT}"'),
        ("assignee=NULL", f'assignee = "{BOT}"'),
        ("ASSIGNEE   IS    EMPTY", f'assignee = "{BOT}"'),
    ]
    items_returned = [source(handler).claimed(jql) for jql, _ in cases]

    assert sent == [expected for _, expected in cases]
    assert [item.id for item in items_returned[0]] == ["VUL-1", "VUL-2"]
    assert items_returned[0][0].title == "CVE-2024-0001 in libfoo"
    assert str(items_returned[0][0].url) == f"{BASE}/browse/VUL-1"


def test_claimed_issues_no_write() -> None:
    """One search, and nothing else. A write here would break the read-only contract."""
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        assert request.method == "POST", "claimed() must never write"
        assert request.url.path == SEARCH_PATH, "the only POST it makes is the search"
        return httpx.Response(200, json={"issues": [issue()]})

    source(handler).claimed("project = VUL AND assignee IS EMPTY")

    assert calls == [("POST", SEARCH_PATH)]


def test_a_query_with_no_empty_assignee_clause_is_a_source_error() -> None:
    """`IS NOT EMPTY` means the opposite, so it is not a match — loud beats a silent 0."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("the query is rejected before any request is made")

    for jql in (
        "project = VUL",
        "project = VUL AND assignee IS NOT EMPTY",
        "project = VUL AND assignee != EMPTY",
    ):
        with pytest.raises(SourceError) as caught:
            source(handler).claimed(jql)
        assert jql in str(caught.value)
        assert "assignee IS EMPTY" in caught.value.fix


# --- lifecycle write-back: annotate and block (ADR-013) ----------------------


def test_annotate_posts_the_comment_as_adf(work_item: WorkItem) -> None:
    """One paragraph per line, in the same document shape `render_adf` reads."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={})

    source(handler).annotate(work_item, "run ended failed\n\nRun logs: https://logs.example/1")

    assert (seen["method"], seen["path"]) == ("POST", "/rest/api/3/issue/VUL-1/comment")
    document = seen["body"]["body"]
    assert document["type"] == "doc"
    texts = [node["content"][0]["text"] for node in document["content"] if node.get("content")]
    assert texts == ["run ended failed", "Run logs: https://logs.example/1"]


def test_annotate_failure_is_logged_and_swallowed(
    work_item: WorkItem, caplog: pytest.LogCaptureFixture
) -> None:
    """A reporting hiccup must not mask the failure it reports."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with caplog.at_level(logging.WARNING):
        source(handler).annotate(work_item, "run ended failed")

    assert any("annotate failed" in record.message for record in caplog.records)


def test_block_adds_the_exclusion_label(work_item: WorkItem) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(204)

    source(handler).block(work_item)

    assert (seen["method"], seen["path"]) == ("PUT", "/rest/api/3/issue/VUL-1")
    assert seen["body"] == {"update": {"labels": [{"add": "tina-blocked"}]}}


def test_the_exclusion_label_is_overridable(work_item: WorkItem) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(204)

    source(handler, blocked_label="factory-hold").block(work_item)

    assert seen["body"] == {"update": {"labels": [{"add": "factory-hold"}]}}


def test_block_is_idempotent(work_item: WorkItem) -> None:
    """Jira's label add is a set add: blocking an already-blocked issue is a no-op."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(204)

    jira = source(handler)
    jira.block(work_item)
    jira.block(work_item)

    assert calls == ["PUT", "PUT"], "the same write twice, and no error either time"


def test_block_failure_is_logged_and_swallowed(
    work_item: WorkItem, caplog: pytest.LogCaptureFixture
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="no permission")

    with caplog.at_level(logging.WARNING):
        source(handler).block(work_item)

    assert any("block failed" in record.message for record in caplog.records)
