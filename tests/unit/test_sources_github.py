from __future__ import annotations

import json
import logging
import sys
from typing import Any

import httpx
import pytest

from tina.models import WorkItem
from tina.query import Assignee, Query
from tina.sources.base import SourceError
from tina.sources.github import GitHubSource, Issue, compile, satisfies

REPO = "acme/api"
BOT = "acme-tina[bot]"

#: A bare repo query: the universal predicates and nothing optional.
Q = Query(scope=REPO)


@pytest.fixture
def item() -> WorkItem:
    return WorkItem(id="acme/api#42", source="github", title="crash on startup")


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

    items = source(handler).query(Q)

    assert seen["path"] == "/search/issues"
    assert seen["q"] == "repo:acme/api is:issue is:open no:assignee"
    assert [i.id for i in items] == ["acme/api#42", "acme/api#43"]
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

    assert source(handler).get(item_id).id == "acme/api#42"


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
    items = source(handler, sleep=waits.append).query(Q)

    assert [i.id for i in items] == ["acme/api#42"]
    assert waits == [60.0]


def test_a_second_secondary_rate_limit_raises_with_the_original_message() -> None:
    """One retry, never a hammer — the second refusal is loud."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text=SECONDARY)

    waits: list[float] = []
    with pytest.raises(SourceError, match="403.*secondary rate limit"):
        source(handler, sleep=waits.append).query(Q)

    assert waits == [60.0]


def test_gateway_errors_are_retried_on_a_short_ladder() -> None:
    responses = iter([httpx.Response(502), httpx.Response(503)])

    def handler(request: httpx.Request) -> httpx.Response:
        return next(responses, httpx.Response(200, json=issue()))

    waits: list[float] = []
    assert source(handler, sleep=waits.append).get("42").id == "acme/api#42"
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


def test_claimed_asks_for_the_token_holder_with_the_rest_untouched() -> None:
    """The assignee node flips; every other qualifier survives. No login lookup."""
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path != "/user", "@me needs no login lookup"
        sent.append(request.url.params["q"])
        return httpx.Response(200, json={"items": [issue(), issue(43)]})

    q = Q.model_copy(update={"labels_all": ("bug",), "labels_none": ("tina-blocked",)})
    items_returned = source(handler, bot_login=None).claimed(q)

    assert sent == ['repo:acme/api is:issue is:open assignee:@me label:"bug" -label:"tina-blocked"']
    assert [i.id for i in items_returned] == ["acme/api#42", "acme/api#43"]
    assert str(items_returned[0].url) == f"https://github.com/{REPO}/issues/42"


def test_claimed_issues_gets_only() -> None:
    """Search is a read. Any other method here would break the read-only contract."""
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        assert request.method == "GET", "claimed() must never write"
        return httpx.Response(200, json={"items": [issue()]})

    source(handler).claimed(Q)

    assert calls == [("GET", "/search/issues")]


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


def test_claimed_under_a_label_claim_requires_the_claim_label() -> None:
    """`-label:x` becomes `label:x`; the blocked label still excludes; still unassigned."""
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.params["q"])
        return httpx.Response(200, json={"items": [issue()]})

    q = Q.model_copy(
        update={"labels_all": ("bug",), "labels_none": ("tina-blocked", "bot-claimed")}
    )
    label_source(handler).claimed(q)

    assert sent == [
        'repo:acme/api is:issue is:open no:assignee label:"bug" label:"bot-claimed"'
        ' -label:"tina-blocked"'
    ]


def test_claimed_under_claim_none_is_empty_without_a_search() -> None:
    """The bot never holds anything, and asking the tracker would imply it could."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("claim = 'none' has no claims to count")

    assert source(handler, claim_policy="none").claimed(Q) == []


# --- matches: the query, evaluated in code against the fetched issue ---------

MATCHES_QUERY = Query(scope=REPO, labels_all=("bug",), labels_none=("tina-blocked",))


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


def test_matches_fetches_and_never_searches() -> None:
    """Search has no number qualifier, so the predicate is evaluated locally."""
    assert matching_source(issue(labels=["bug"])).matches("42", MATCHES_QUERY) is True


def test_satisfies_compares_labels_case_insensitively() -> None:
    fetched = Issue.model_validate(issue(labels=["Bug"]))

    assert satisfies(fetched, Query(scope=REPO, labels_all=("bug",))) is True
    assert satisfies(fetched, Query(scope=REPO, labels_none=("BUG",))) is False


def test_satisfies_requires_every_label() -> None:
    fetched = Issue.model_validate(issue(labels=["triaged"]))

    assert satisfies(fetched, Query(scope=REPO, labels_all=("triaged", "fix"))) is False


# --- compile: the predicate tree, as issue search --------------------------------


def test_compile_quotes_labels_and_excludes_markers() -> None:
    q = Query(scope=REPO, labels_all=("needs triage", "bug"), labels_none=("tina-blocked", "x"))

    assert compile(q) == (
        'repo:acme/api is:issue is:open no:assignee label:"needs triage" label:"bug"'
        ' -label:"tina-blocked" -label:"x"'
    )


def test_compile_with_nothing_optional() -> None:
    assert compile(Q) == "repo:acme/api is:issue is:open no:assignee"


def test_compile_refuses_what_search_cannot_express() -> None:
    """Config rejects these first; the compiler refuses rather than silently dropping a node."""
    with pytest.raises(SourceError, match="cannot compile filters"):
        compile(Query(scope=REPO, fields={"Team": ("a",)}))
    with pytest.raises(SourceError, match="cannot compile extra, status"):
        compile(Query(scope=REPO, status="Open", extra="x"))
    with pytest.raises(SourceError, match="no item qualifier"):
        compile(Q.scoped_to("42"))
    with pytest.raises(SourceError, match="cannot express assignee"):
        compile(Query(scope=REPO, assignee=Assignee.UNASSIGNED_OR_ME))


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


# --- credentials and login ------------------------------------------------------


def test_gh_token_is_accepted_as_the_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "ghp_from_the_cli_convention")

    src = GitHubSource(repo=REPO)

    assert src.client.headers["Authorization"] == "Bearer ghp_from_the_cli_convention"


def test_github_token_wins_over_gh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "canonical")
    monkeypatch.setenv("GH_TOKEN", "alternative")

    assert GitHubSource(repo=REPO).client.headers["Authorization"] == "Bearer canonical"


def test_the_error_names_the_canonical_variable() -> None:
    with pytest.raises(SourceError, match="GITHUB_TOKEN"):
        GitHubSource(repo=REPO)


def test_login_reports_who_the_token_acts_as() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/user"
        return httpx.Response(200, json={"login": "acme-bot"})

    assert source(handler, bot_login=None).login() == "acme-bot"


# --- short-lived tokens ---------------------------------------------------------


def _token_command(monkeypatch: pytest.MonkeyPatch, tokens: list[str], tmp_path) -> None:
    """A fake mint: each run hands out the next token in the list."""
    counter = tmp_path / "n"
    script = tmp_path / "mint.py"
    script.write_text(
        "import pathlib\n"
        f"counter = pathlib.Path({str(counter)!r})\n"
        "n = int(counter.read_text()) if counter.exists() else 0\n"
        "counter.write_text(str(n + 1))\n"
        f"print({tokens!r}[n])\n"
    )
    monkeypatch.setenv("GITHUB_TOKEN_COMMAND", f"{sys.executable} {script}")


def test_the_token_command_supplies_the_initial_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _token_command(monkeypatch, ["ghs_first"], tmp_path)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    src = GitHubSource(repo=REPO)

    assert src.client.headers["Authorization"] == "Bearer ghs_first"


def test_a_401_re_mints_and_retries_once(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """The token aged out mid-run: mint again, retry the request, carry on."""
    _token_command(monkeypatch, ["ghs_fresh"], tmp_path)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers["Authorization"]
        seen.append(auth)
        if auth == "Bearer ghs_fresh":
            return httpx.Response(200, json=issue())
        return httpx.Response(401, json={"message": "Bad credentials"})

    client = httpx.Client(
        transport=httpx.MockTransport(handler), headers={"Authorization": "Bearer ghs_stale"}
    )
    src = GitHubSource(repo=REPO, client=client, bot_login=BOT)

    assert src.get("42").id == "acme/api#42"
    assert seen == ["Bearer ghs_stale", "Bearer ghs_fresh"], "one retry, with the fresh token"
    assert src.client.headers["Authorization"] == "Bearer ghs_fresh", "kept for later calls"


def test_a_second_401_is_a_real_refusal(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _token_command(monkeypatch, ["ghs_fresh"], tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    client = httpx.Client(
        transport=httpx.MockTransport(handler), headers={"Authorization": "Bearer ghs_stale"}
    )
    with pytest.raises(SourceError, match="401"):
        GitHubSource(repo=REPO, client=client, bot_login=BOT).get("42")


def test_without_a_token_command_a_401_is_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401)

    with pytest.raises(SourceError, match="401"):
        source(handler).get("42")
    assert calls == 1
