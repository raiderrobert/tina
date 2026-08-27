from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest

from tina.models import WorkItem
from tina.sources.base import SourceError
from tina.sources.github import NO_ASSIGNEE, GitHubSource

REPO = "acme/api"
BOT = "acme-tina[bot]"


@pytest.fixture
def item() -> WorkItem:
    return WorkItem(id="42", source="github", title="crash on startup")


def issue(
    number: int = 42,
    assignees: list[str] | None = None,
    labels: list[str] | None = None,
    state: str = "open",
) -> dict[str, Any]:
    return {
        "number": number,
        "title": "crash on startup",
        "body": "stack trace follows",
        "html_url": f"https://github.com/{REPO}/issues/{number}",
        "assignees": [{"login": login} for login in assignees or []],
        "labels": [{"name": name} for name in labels or []],
        "state": state,
    }


def source(handler, bot_login: str | None = BOT, **kwargs: Any) -> GitHubSource:
    return GitHubSource(
        repo=REPO,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        bot_login=bot_login,
        **kwargs,
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
    assert str(items[0].url) == f"https://github.com/{REPO}/issues/42"
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


def test_claim_prognosis_reports_the_holder_without_writing(item: WorkItem) -> None:
    """Unlike Jira, the bot already holding it alone is a claim that would succeed."""
    calls: list[str] = []

    def responder(assignees: list[str]):
        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.method)
            assert request.method == "GET", "claim_prognosis must never write"
            return httpx.Response(200, json=issue(assignees=assignees))

        return handler

    free = source(responder([])).claim_prognosis(item)
    ours = source(responder([BOT])).claim_prognosis(item)
    theirs = source(responder(["alice", "bob"])).claim_prognosis(item)

    assert (free.would_claim, free.holder) == (True, "")
    assert (ours.would_claim, ours.holder) == (True, BOT)
    assert (theirs.would_claim, theirs.holder) == (False, "alice, bob")
    assert calls == ["GET", "GET", "GET"], "one read each, and nothing else"


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


# --- transient retry: the failures that look like an empty backlog -----------

SECONDARY = "You have exceeded a secondary rate limit. Please wait a few minutes."


def test_a_secondary_rate_limit_is_retried_once_after_the_documented_wait() -> None:
    responses = iter([httpx.Response(403, text=SECONDARY)])

    def handler(request: httpx.Request) -> httpx.Response:
        return next(responses, httpx.Response(200, json={"items": [issue()]}))

    waits: list[float] = []
    items = source(handler, sleep=waits.append).query("repo:acme/api no:assignee")

    assert [i.id for i in items] == ["42"]
    assert waits == [60.0]


def test_a_second_secondary_rate_limit_raises_with_the_original_message() -> None:
    """One retry, never a hammer — the second refusal is loud."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text=SECONDARY)

    waits: list[float] = []
    with pytest.raises(SourceError, match="403.*secondary rate limit"):
        source(handler, sleep=waits.append).query("repo:acme/api no:assignee")

    assert waits == [60.0]


def test_gateway_errors_are_retried_on_a_short_ladder() -> None:
    responses = iter([httpx.Response(502), httpx.Response(503)])

    def handler(request: httpx.Request) -> httpx.Response:
        return next(responses, httpx.Response(200, json=issue()))

    waits: list[float] = []
    assert source(handler, sleep=waits.append).get("42").id == "42"
    assert waits == [2.0, 8.0]


def test_a_persistent_gateway_error_raises_after_the_ladder() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream connect error")

    waits: list[float] = []
    with pytest.raises(SourceError, match="503: upstream connect error"):
        source(handler, sleep=waits.append).get("42")

    assert waits == [2.0, 8.0]


def test_an_ordinary_403_is_not_retried() -> None:
    """Only the documented marker means the rate limit; a plain 403 is permanent."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Resource not accessible by integration")

    waits: list[float] = []
    with pytest.raises(SourceError, match="403"):
        source(handler, sleep=waits.append).get("42")

    assert waits == []


def test_other_4xx_are_never_retried() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    waits: list[float] = []
    with pytest.raises(SourceError, match="404"):
        source(handler, sleep=waits.append).get("42")

    assert waits == []


def test_an_issue_without_a_number_is_a_source_error() -> None:
    """Nothing can be claimed or linked without one."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"title": "no number here"})

    with pytest.raises(SourceError, match="unexpected response"):
        source(handler).get("42")


def test_a_missing_html_url_becomes_none_not_empty_string() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = issue()
        del payload["html_url"]
        return httpx.Response(200, json=payload)

    assert source(handler).get("42").url is None


def test_claimed_swaps_the_no_assignee_qualifier_in_place() -> None:
    """Other qualifiers unmoved — including a label that merely contains the text."""
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.params["q"])
        return httpx.Response(200, json={"items": [issue(), issue(43)]})

    cases = [
        # the README's example query
        (
            "repo:acme/api is:issue is:open no:assignee label:bug",
            f"repo:acme/api is:issue is:open assignee:{BOT} label:bug",
        ),
        # a label whose value is the qualifier's own text is a different token
        (
            'repo:acme/api no:assignee label:"no:assignee"',
            f'repo:acme/api assignee:{BOT} label:"no:assignee"',
        ),
        ("no:assignee", f"assignee:{BOT}"),
        ("repo:acme/api NO:ASSIGNEE is:open", f"repo:acme/api assignee:{BOT} is:open"),
    ]
    items_returned = [source(handler).claimed(q) for q, _ in cases]

    assert sent == [expected for _, expected in cases]
    assert [i.id for i in items_returned[0]] == ["42", "43"]
    assert str(items_returned[0][0].url) == f"https://github.com/{REPO}/issues/42"


def test_claimed_issues_gets_only() -> None:
    """Search is a read. Any other method here would break the read-only contract."""
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        assert request.method == "GET", "claimed() must never write"
        return httpx.Response(200, json={"items": [issue()]})

    source(handler).claimed("repo:acme/api is:open no:assignee")

    assert calls == [("GET", "/search/issues")]


def test_a_query_with_no_no_assignee_qualifier_is_a_source_error() -> None:
    """A literal containing the text is not the qualifier, and neither is a negated one."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("the query is rejected before any request is made")

    for q in (
        "repo:acme/api is:open label:bug",
        'repo:acme/api is:open label:"no:assignee"',
        "repo:acme/api is:open -no:assignee",
    ):
        with pytest.raises(SourceError) as caught:
            source(handler).claimed(q)
        assert q in str(caught.value)
        assert NO_ASSIGNEE in caught.value.fix


# --- claim policies: label claims for App tokens that cannot assign (ADR-014) -


def label_source(handler) -> GitHubSource:
    return source(handler, claim_policy="label", claim_label="bot-claimed")


def test_a_label_claim_adds_the_label_then_confirms(item: WorkItem) -> None:
    state: dict[str, list[str]] = {"labels": []}
    calls: list[tuple[str, str]] = []
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            seen["body"] = json.loads(request.content)
            state["labels"] = ["bot-claimed"]
            return httpx.Response(200, json=[{"name": "bot-claimed"}])
        return httpx.Response(200, json=issue(labels=state["labels"]))

    assert label_source(handler).claim(item) is True
    assert calls == [
        ("GET", f"/repos/{REPO}/issues/42"),
        ("POST", f"/repos/{REPO}/issues/42/labels"),
        ("GET", f"/repos/{REPO}/issues/42"),
    ]
    assert seen["body"] == {"labels": ["bot-claimed"]}


def test_a_label_claim_refuses_an_already_labeled_issue(item: WorkItem) -> None:
    """The label carries no identity, so present always means someone else holds it."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(200, json=issue(labels=["bot-claimed"]))

    assert label_source(handler).claim(item) is False
    assert calls == ["GET"], "a labeled issue is never written to"


def test_a_label_claim_fails_when_the_write_did_not_stick(item: WorkItem) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=issue(labels=[]))

    assert label_source(handler).claim(item) is False


def test_claim_prognosis_under_a_label_claim(item: WorkItem) -> None:
    def held(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET", "claim_prognosis must never write"
        return httpx.Response(200, json=issue(labels=["bot-claimed", "bug"]))

    def free(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET", "claim_prognosis must never write"
        return httpx.Response(200, json=issue(labels=["bug"]))

    taken = label_source(held).claim_prognosis(item)
    unheld = label_source(free).claim_prognosis(item)

    assert (taken.would_claim, taken.holder) == (False, "label:bot-claimed")
    assert (unheld.would_claim, unheld.holder) == (True, "")


def test_claimed_under_a_label_claim_inverts_the_negated_label_token() -> None:
    """`-label:x` becomes `label:x`; every other qualifier stays put."""
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.params["q"])
        return httpx.Response(200, json={"items": [issue()]})

    cases = [
        (
            "repo:acme/api is:issue is:open -label:bot-claimed label:bug",
            "repo:acme/api is:issue is:open label:bot-claimed label:bug",
        ),
        ('repo:acme/api -label:"bot-claimed"', "repo:acme/api label:bot-claimed"),
        ("-LABEL:BOT-CLAIMED", "label:bot-claimed"),
    ]
    for q, _ in cases:
        label_source(handler).claimed(q)

    assert sent == [expected for _, expected in cases]


def test_a_query_with_no_negated_claim_label_is_a_source_error() -> None:
    """A different label's negation is not the claim label's."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("the query is rejected before any request is made")

    for q in ("repo:acme/api is:open", "repo:acme/api -label:other-label"):
        with pytest.raises(SourceError) as caught:
            label_source(handler).claimed(q)
        assert q in str(caught.value)
        assert "-label:bot-claimed" in caught.value.fix


def test_claimed_under_claim_none_is_empty_without_a_search() -> None:
    """The bot never holds anything, and asking the tracker would imply it could."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("claim = 'none' has no claims to count")

    assert source(handler, claim_policy="none").claimed("repo:acme/api is:open") == []


# --- matches: the query's structured qualifiers, re-checked in code ----------

MATCHES_QUERY = "repo:acme/api is:issue is:open no:assignee label:bug -label:tina-blocked"


def matching_source(payload: dict[str, Any]) -> GitHubSource:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET", "matches must never write"
        assert request.url.path == f"/repos/{REPO}/issues/42", "one fetch, no search"
        return httpx.Response(200, json=payload)

    return source(handler)


def test_a_still_eligible_issue_matches() -> None:
    payload = issue(labels=["bug"], state="open")

    assert matching_source(payload).matches("42", MATCHES_QUERY) is True


def test_a_closed_issue_no_longer_matches() -> None:
    payload = issue(labels=["bug"], state="closed")

    assert matching_source(payload).matches("42", MATCHES_QUERY) is False


def test_an_assigned_issue_no_longer_matches() -> None:
    payload = issue(assignees=["alice"], labels=["bug"])

    assert matching_source(payload).matches("42", MATCHES_QUERY) is False


def test_an_issue_without_the_required_label_no_longer_matches() -> None:
    assert matching_source(issue(labels=[])).matches("42", MATCHES_QUERY) is False


def test_an_issue_carrying_a_negated_label_no_longer_matches() -> None:
    """The blocked or claim label arrived between dispatch and worker start."""
    payload = issue(labels=["bug", "tina-blocked"])

    assert matching_source(payload).matches("42", MATCHES_QUERY) is False


def test_matches_reads_quoted_label_tokens() -> None:
    payload = issue(labels=["bug"])

    assert matching_source(payload).matches("42", 'is:open label:"bug"') is True
    assert matching_source(payload).matches("42", 'is:open -label:"bug"') is False


def test_unstructured_qualifiers_are_the_dispatch_querys_job() -> None:
    """repo: and is:issue were true at dispatch and cannot silently change."""
    payload = issue(labels=["bug"])

    assert matching_source(payload).matches("42", "repo:acme/api is:issue label:bug") is True


# --- lifecycle write-back: annotate and block (ADR-013) ----------------------


def test_annotate_posts_an_issue_comment(item: WorkItem) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={})

    source(handler).annotate(item, "run ended failed\n\nRun logs: https://logs.example/1")

    assert (seen["method"], seen["path"]) == ("POST", f"/repos/{REPO}/issues/42/comments")
    assert seen["body"] == {"body": "run ended failed\n\nRun logs: https://logs.example/1"}


def test_annotate_failure_is_logged_and_swallowed(
    item: WorkItem, caplog: pytest.LogCaptureFixture
) -> None:
    """A reporting hiccup must not mask the failure it reports."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with caplog.at_level(logging.WARNING):
        source(handler).annotate(item, "run ended failed")

    assert any("annotate failed" in record.message for record in caplog.records)


def test_block_adds_the_exclusion_label(item: WorkItem) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=[{"name": "tina-blocked"}])

    source(handler).block(item)

    assert (seen["method"], seen["path"]) == ("POST", f"/repos/{REPO}/issues/42/labels")
    assert seen["body"] == {"labels": ["tina-blocked"]}


def test_the_exclusion_label_is_overridable(item: WorkItem) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=[])

    source(handler, blocked_label="factory-hold").block(item)

    assert seen["body"] == {"labels": ["factory-hold"]}


def test_block_is_idempotent(item: WorkItem) -> None:
    """GitHub's label add returns the full set: adding an existing label is a no-op."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(200, json=[{"name": "tina-blocked"}])

    github = source(handler)
    github.block(item)
    github.block(item)

    assert calls == ["POST", "POST"], "the same write twice, and no error either time"


def test_block_failure_is_logged_and_swallowed(
    item: WorkItem, caplog: pytest.LogCaptureFixture
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="no permission")

    with caplog.at_level(logging.WARNING):
        source(handler).block(item)

    assert any("block failed" in record.message for record in caplog.records)
