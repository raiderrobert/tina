"""The query IR: what a track asks a tracker for, before any tracker syntax.

A track gives the parts of its query — a project plus a set of opted-in
teams, a repo plus a label set — and `build` turns them into a `Query`: a
small tree of predicates that every source compiles to its own syntax. The
universal predicates every track would otherwise re-type (the queued status,
unassigned, not blocked, not claimed, oldest first) are nodes here, owned in
one place.

The two operations Tina performs on a query are operations on the tree, not
on a string: `held_by` flips the exclusion node to find what the bot holds
(`claimed`), and `scoped_to` adds the one-item node for the eligibility
re-check (`matches`). Because the nodes are structured there is nothing to
tokenize or regex, and no quoted literal can be mistaken for a clause.

A source declares which optional nodes it compiles (`SOURCE_FEATURES`). A
track using one its source lacks fails at config load, naming the key. The
table lives here rather than on the adapters because `tina.config` needs it
without importing an adapter, and `tina.sources` imports `tina.config`.

Every value that reaches a `Query` has been validated by the config model
(charset, shape), so compilers concatenate without escaping. Nothing here
touches a tracker.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from tina.config import TrackConfig


class Feature(StrEnum):
    """An optional query node, named by the config key that produces it."""

    STATUS = "status"
    FIELDS = "filters"
    EXTRA = "extra"
    LABELS = "labels"


#: What each source compiles. A track whose query uses a feature outside its
#: source's set is rejected at load; a compiler handed one raises. Adding a
#: feature to a source is extending its `compile` and this set — no config
#: change.
SOURCE_FEATURES: dict[str, frozenset[Feature]] = {
    "jira": frozenset({Feature.STATUS, Feature.FIELDS, Feature.EXTRA, Feature.LABELS}),
    "github": frozenset({Feature.LABELS}),
}


class Assignee(StrEnum):
    """The assignment predicate — the node `held_by` flips."""

    #: Nobody holds it: the queue.
    UNASSIGNED = "unassigned"
    #: Nobody, or the bot: under a claim transition the status, not the
    #: assignee, excludes a claimed item, so a bot-held item still in the
    #: queued status was reopened upstream and belongs in the queue again.
    UNASSIGNED_OR_ME = "unassigned_or_me"
    #: The bot holds it: what `claimed` asks.
    ME = "me"


class Query(BaseModel):
    """One track's predicate tree.

    `scope` is where the tracker looks — a Jira project key, a GitHub
    `owner/name`. `labels_none` carries the markers that take an item out of
    the queue (blocked, claimed); `labels_all` the labels a track requires.
    `extra` is native text the compiler appends verbatim and never reads —
    the escape hatch, kept out of every node Tina has to rewrite. `item` is
    set only by `scoped_to`.
    """

    model_config = ConfigDict(frozen=True)

    scope: str
    assignee: Assignee = Assignee.UNASSIGNED
    status: str | None = None
    fields: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    labels_all: tuple[str, ...] = ()
    labels_none: tuple[str, ...] = ()
    extra: str | None = None
    item: str | None = None

    def features(self) -> frozenset[Feature]:
        """The optional nodes this query uses."""
        used = set()
        if self.status is not None:
            used.add(Feature.STATUS)
        if self.fields:
            used.add(Feature.FIELDS)
        if self.extra is not None:
            used.add(Feature.EXTRA)
        if self.labels_all:
            used.add(Feature.LABELS)
        return frozenset(used)

    def held_by(self, claim_policy: str, claim_label: str | None) -> Query:
        """The same query with its exclusion inverted: what the bot holds.

        Under assign the assignee node becomes the bot. Under label the claim
        label moves from excluded to required — a label carries no identity,
        so this is every item anyone claimed with it. Under `none` the bot
        never holds anything; callers answer that without a query, and asking
        here is a bug.
        """
        if claim_policy == "label":
            return self.model_copy(
                update={
                    "labels_none": tuple(x for x in self.labels_none if x != claim_label),
                    "labels_all": (*self.labels_all, str(claim_label)),
                }
            )
        if claim_policy == "assign":
            return self.model_copy(update={"assignee": Assignee.ME})
        raise ValueError(f"claim = {claim_policy!r} holds nothing; there is no query to invert")

    def scoped_to(self, item_id: str) -> Query:
        """The same predicate, restricted to one item — the eligibility re-check."""
        return self.model_copy(update={"item": item_id})


def build(track: TrackConfig) -> Query:
    """A track's query, from the parts its config gives.

    Owns the universal predicates so no track re-types them: the queued
    status, unassigned (or bot-held under a claim transition), the blocked
    marker excluded unless a blocked transition stands in for it, and the
    claim label excluded under a label claim.
    """
    if track.mode == "sweep" or track.source is None:
        raise ValueError(f'track {track.name!r} has no query (mode = "sweep")')
    assignee = Assignee.UNASSIGNED
    if track.claim == "assign" and track.claim_transition:
        assignee = Assignee.UNASSIGNED_OR_ME
    blocked = None if track.blocked_transition else track.blocked_label
    claim = track.claim_label if track.claim == "label" else None
    return Query(
        scope=(track.project if track.source == "jira" else track.repo) or "",
        assignee=assignee,
        status=track.status,
        fields={field: tuple(values) for field, values in track.filters.items()},
        labels_all=tuple(track.labels),
        labels_none=tuple(label for label in (blocked, claim) if label),
        extra=track.extra,
    )
