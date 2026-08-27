# 14. Claim policy is per track: assign, label, or none

**Status:** Proposed

**Date:** 2026-08-27

## Context

ADR-004 rules that the worker claims and the tracker is the ledger, and the
only claim it knew was assignment. Running a production registry through that
model found three claim shapes assignment cannot cover. GitHub App
installation tokens cannot assign issues, so those deployments claim by label
— claims Tina could not see. Triage tracks that only leave a comment must not
claim at all, because assigning the bot tells a human the wrong owner. And
Jira tracks want a status transition after the claim, so the queued status
stays truthful and humans can requeue by transition. Assignment itself also
had a deadlock: `claim` refused every existing assignee, the bot included, so
an item reopened while the bot still held it could never be re-claimed.

## Decision

The claim strategy is track configuration, not adapter behavior. `claim`
selects one of three strategies; the ledger stays the tracker either way.

- `"assign"` (default) — assignment, as before, but idempotent: an item the
  bot already holds claims successfully.
- `"label"` — the claim is `claim_label` on the item. The track query must
  exclude the label; `claimed()` and `status` invert that negated label token
  instead of the assignee clause.
- `"none"` — no claim and no prognosis. Dedupe is the query's job.

`claim_transition` (Jira only, invalid elsewhere) names a transition applied
after a successful claim, best-effort like the other lifecycle writes.

## Consequences

- ADR-004's failure-mode analysis is unchanged: duplicate workers stay the
  tolerated, self-healing failure mode under every strategy.
- `claim = "none"` widens the duplicate window from the claim race to the
  whole dispatch cycle. That is the query author's trade to make, and the
  eligibility re-check at worker start is what keeps it survivable.
- A label claim carries no identity, so a present claim label always reads as
  "someone else holds it" — the idempotent re-claim exists only for assign.
- Invalid combinations (`claim_label` without `"label"`, `claim_transition`
  on GitHub or under `"none"`) fail at config load, naming the track.
