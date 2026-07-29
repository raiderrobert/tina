from __future__ import annotations

from typing import Any

import httpx
import pytest

from tina.models import WorkItem
from tina.sources.base import SourceError
from tina.sources.github import GitHubSource

REPO = "acme/api"
BOT = "acme-tina[bot]"


@pytest.fixture
def item() -> WorkItem:
    return WorkItem(id="42", source="github", title="crash on startup")


def issue(number: int = 42, assignees: list[str] | None = None) -> dict[str, Any]:
    return {
        "number": number,
        "title": "crash on startup",
        "body": "stack trace follows",
        "html_url": f"https://github.com/{REPO}/issues/{number}",
        "assignees": [{"login": login} for login in assignees or []],
    }


def source(handler, bot_login: str | None = BOT) -> GitHubSource:
    return GitHubSource(
        repo=REPO,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        bot_login=bot_login,
    )


def test_query_returns_normalized_items() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["q"] = request.url.params["q"]
        return httpx.Response(200, json={"items": [issue(), issue(43)]})

    items = source(handler).query("repo:acme/api is:open")

    assert seen["path"] == "/search/issues"
    assert seen["q"] == "repo:acme/api is:open"
    assert [i.id for i in items] == ["42", "43"]
    assert items[0].url == f"https://github.com/{REPO}/issues/42"
    assert items[0].description == "stack trace follows"


def test_claim_succeeds_when_bot_is_sole_assignee(item: WorkItem) -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(201, json=issue(assignees=[BOT]))
        return httpx.Response(200, json=issue(assignees=[BOT]))

    assert source(handler).claim(item) is True
    assert calls == [
        ("POST", f"/repos/{REPO}/issues/42/assignees"),
        ("GET", f"/repos/{REPO}/issues/42"),
    ]


def test_claim_fails_when_someone_else_is_co_assigned(item: WorkItem) -> None:
    """Assignment is an idempotent add, so a co-assignee means we lost the race."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json={})
        return httpx.Response(200, json=issue(assignees=["someone-else", BOT]))

    assert source(handler).claim(item) is False


def test_claim_fails_when_the_add_did_not_land(item: WorkItem) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json={})
        return httpx.Response(200, json=issue(assignees=[]))

    assert source(handler).claim(item) is False


def test_bot_login_is_looked_up_when_unset(item: WorkItem) -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": BOT})
        if request.method == "POST":
            return httpx.Response(201, json={})
        return httpx.Response(200, json=issue(assignees=[BOT]))

    assert source(handler, bot_login=None).claim(item) is True
    assert "/user" in paths


@pytest.mark.parametrize("item_id", ["42", "#42", "acme/api#42"])
def test_item_ids_are_accepted_in_several_shapes(item_id: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/repos/{REPO}/issues/42"
        return httpx.Response(200, json=issue())

    assert source(handler).get(item_id).id == "42"


def test_repo_is_required() -> None:
    with pytest.raises(SourceError, match="repo"):
        GitHubSource(repo="", client=httpx.Client())


def test_http_error_is_a_source_error(item: WorkItem) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    with pytest.raises(SourceError, match="404"):
        source(handler).get("42")
