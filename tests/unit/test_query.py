from __future__ import annotations

from tina import query


def test_jira_query_builds_the_universal_predicates_in_order() -> None:
    jql = query.jira_query(
        "VUL",
        None,
        {"Team": ["Payments", "Search"]},
        "labels not in (wontfix)",
        claim_policy="assign",
        claim_label=None,
        claim_transition=None,
        blocked_label="tina-blocked",
    )

    assert jql == (
        'project = VUL AND status = "Open" AND "Team" in ("Payments", "Search")'
        ' AND assignee IS EMPTY AND (labels IS EMPTY OR labels not in ("tina-blocked"))'
        " AND (labels not in (wontfix)) ORDER BY created ASC"
    )


def test_jira_query_offers_back_bot_held_items_under_a_claim_transition() -> None:
    jql = query.jira_query(
        "VUL",
        "Triage",
        {},
        None,
        claim_policy="assign",
        claim_label=None,
        claim_transition="In Progress",
        blocked_label=None,
    )

    assert 'status = "Triage"' in jql
    assert "(assignee IS EMPTY OR assignee = currentUser())" in jql
    assert "labels" not in jql, "no blocked label means no label guard"


def test_jira_query_excludes_the_claim_label_under_label_claims() -> None:
    jql = query.jira_query(
        "BUGS",
        None,
        {},
        None,
        claim_policy="label",
        claim_label="bot-claimed",
        claim_transition=None,
        blocked_label="tina-blocked",
    )

    assert 'labels not in ("tina-blocked", "bot-claimed")' in jql
    assert "currentUser" not in jql


def test_github_query_quotes_labels_and_excludes_markers() -> None:
    q = query.github_query(
        "acme/api", ["needs triage", "bug"], claim_label="bot-claimed", blocked_label="tina-blocked"
    )

    assert q == (
        'repo:acme/api is:issue is:open no:assignee label:"needs triage" label:"bug"'
        ' -label:"tina-blocked" -label:"bot-claimed"'
    )


def test_github_query_with_nothing_optional() -> None:
    q = query.github_query("acme/api", [], claim_label=None, blocked_label=None)

    assert q == "repo:acme/api is:issue is:open no:assignee"
