from __future__ import annotations

from typing import Any

import pytest

from tina import config, query
from tina.query import Assignee, Feature, Query


def track(**overrides: Any) -> config.TrackConfig:
    data: dict[str, Any] = {"name": "vul", "track": "vul", "source": "jira", "project": "VUL"}
    return config.TrackConfig(**{**data, **overrides})


# --- build: config parts -> predicate tree -----------------------------------


def test_build_owns_the_universal_predicates() -> None:
    q = query.build(track(filters={"Team": ["Payments", "Search"]}, extra="labels not in (x)"))

    assert q == Query(
        scope="VUL",
        assignee=Assignee.UNASSIGNED,
        status=None,
        fields={"Team": ("Payments", "Search")},
        labels_none=("tina-blocked",),
        extra="labels not in (x)",
    )


def test_build_offers_back_bot_held_items_under_a_claim_transition() -> None:
    q = query.build(track(status="Triage", claim_transition="In Progress"))

    assert q.assignee is Assignee.UNASSIGNED_OR_ME
    assert q.status == "Triage"


def test_build_excludes_the_claim_label_under_label_claims() -> None:
    q = query.build(track(claim="label", claim_label="bot-claimed"))

    assert q.labels_none == ("tina-blocked", "bot-claimed")
    assert q.assignee is Assignee.UNASSIGNED


def test_build_drops_the_blocked_label_under_a_blocked_transition() -> None:
    q = query.build(track(blocked_transition="Blocked"))

    assert q.labels_none == ()


def test_build_scopes_a_github_track_by_repo() -> None:
    q = query.build(track(source="github", project=None, repo="acme/api", labels=["bug"]))

    assert (q.scope, q.labels_all) == ("acme/api", ("bug",))


def test_a_sweep_has_no_query() -> None:
    sweep = config.TrackConfig(name="audit", track="audit", mode="sweep")

    with pytest.raises(ValueError, match="sweep"):
        query.build(sweep)


# --- features: what a query asks of its source --------------------------------


def test_features_names_only_the_optional_nodes_in_use() -> None:
    assert Query(scope="VUL").features() == frozenset()
    assert Query(scope="VUL", status="Open").features() == {Feature.STATUS}
    assert Query(scope="VUL", fields={"Team": ("a",)}).features() == {Feature.FIELDS}
    assert Query(scope="VUL", extra="x").features() == {Feature.EXTRA}
    assert Query(scope="r", labels_all=("bug",)).features() == {Feature.LABELS}


def test_exclusion_markers_are_universal_not_a_feature() -> None:
    """Every source must be able to exclude the blocked and claim labels."""
    assert Query(scope="r", labels_none=("tina-blocked",)).features() == frozenset()


def test_every_source_declares_its_features() -> None:
    assert set(query.SOURCE_FEATURES) == set(config.SOURCES)


# --- held_by: the exclusion, inverted ------------------------------------------


def test_held_by_under_assign_flips_the_assignee_node() -> None:
    q = Query(scope="VUL", labels_none=("tina-blocked",), extra="x")

    held = q.held_by("assign", None)

    assert held.assignee is Assignee.ME
    assert (held.labels_none, held.extra) == (("tina-blocked",), "x"), "the rest is untouched"


def test_held_by_under_a_claim_transition_still_asks_for_the_bot_alone() -> None:
    q = Query(scope="VUL", assignee=Assignee.UNASSIGNED_OR_ME)

    assert q.held_by("assign", None).assignee is Assignee.ME


def test_held_by_under_label_moves_the_claim_label_to_required() -> None:
    q = Query(scope="r", labels_all=("bug",), labels_none=("tina-blocked", "bot-claimed"))

    held = q.held_by("label", "bot-claimed")

    assert held.labels_none == ("tina-blocked",), "the blocked label still excludes"
    assert held.labels_all == ("bug", "bot-claimed")
    assert held.assignee is Assignee.UNASSIGNED, "a label carries no identity"


def test_held_by_under_none_is_a_bug() -> None:
    with pytest.raises(ValueError, match="holds nothing"):
        Query(scope="VUL").held_by("none", None)


# --- scoped_to: one item -------------------------------------------------------


def test_scoped_to_adds_the_item_and_changes_nothing_else() -> None:
    q = Query(scope="VUL", status="Open", fields={"Team": ("a",)})

    scoped = q.scoped_to("VUL-1")

    assert scoped.item == "VUL-1"
    assert scoped.model_copy(update={"item": None}) == q


def test_queries_are_immutable() -> None:
    q = Query(scope="VUL")

    with pytest.raises(Exception, match="frozen"):
        setattr(q, "scope", "BUGS")  # noqa: B010 - the point is the runtime refusal
