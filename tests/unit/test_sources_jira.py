from __future__ import annotations

from typing import Any

import httpx
import pytest

from tina.models import WorkItem
from tina.sources.base import SourceError
from tina.sources.jira import JiraSource, render_adf

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
