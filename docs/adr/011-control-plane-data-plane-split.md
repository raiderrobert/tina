# 11. Split decisions into a control plane and a data plane

**Status:** Proposed

**Date:** 2026-08-27

## Context

Every decision Tina makes falls on one of two sides. Decisions made per work
item — claim it, build the prompt, run the agent, record the outcome — are the
data plane. Decisions made across work items — what tracks exist, whether
dispatch runs at all, how many workers to fan out — are the control plane.
Nothing straddles the line, which is the test that the boundary is real rather
than imposed.

Stated as surfaces, the control plane has four. Tina has two of them:

| Surface | What it does | Status |
|---|---|---|
| Declaration | What exists: tracks, sources, queries, adapters | Exists (`config.py`) |
| Policy | Runtime gates: paused, concurrency, budget. The only thing that changes without a deploy | Missing |
| Admission | Reject bad config and bad tracks before they reach the data plane | Missing |
| Introspection | What is configured, what is queued, what is in flight | Half — `status` |

The data plane already has its two roles, dispatcher and worker, split by
[ADR-003](003-dispatch-worker-split.md). It is tempting to map that split onto
the plane boundary, because there are already two parts. That mapping is
wrong. The dispatcher consumes control decisions; it does not make them.
Treating the dispatcher as the control plane is how state ends up in the
dispatcher, which breaks "the tracker is the ledger"
([ADR-004](004-worker-side-claiming.md)). Three parts, not two: control plane,
dispatcher, worker.

## Decision

Adopt the split, the four surfaces, and six invariants. Each invariant decides
something concrete downstream.

- **I1 — Control is read at dispatch, never mid-run.** An in-flight worker
  completes even when the control plane is unavailable. An item already
  claimed is worth more finished than stopped.
- **I2 — The control plane fails closed.** The opposite of a network control
  plane, which fails static. The blast radius of running unsupervised exceeds
  the blast radius of not running; a skipped cycle costs one poll interval.
- **I3 — The control plane never learns work-item content.** Routing by
  content would reintroduce the classification problem that was deliberately
  pushed into the skill, and would make Tina non-deterministic in the one
  place it is clean (architecture §7).
- **I4 — No persistent state in either plane.** The tracker is the ledger;
  policy is a file; audit is emitted, not stored
  ([ADR-004](004-worker-side-claiming.md)).
- **I5 — Policy is not schedule.** Pause and throttle gate an invocation the
  external scheduler already made.
  [ADR-002](002-no-scheduling-ownership.md) stands.
- **I6 — Tina owns no transport.** It reads paths; the deployment decides how
  the bytes arrive ([ADR-008](008-tracks-installed-via-napoln.md) precedent).

## Consequences

- Where a new config key belongs is now decidable: ask which plane reads it,
  and how fast it must change.
- Configuration splits into three tiers by change velocity:

  | Tier | Contents | Location | Velocity | Changed by |
  |---|---|---|---|---|
  | Adapter | `harness`, `executor`, their tables | `tina.toml`, in the image | image build | platform owner |
  | Declaration | track tables | `tina.toml`, in the image | PR | track authors |
  | Policy | `paused`, `max_concurrency` | control file, mounted | minutes, no deploy | whoever holds the pager |

  Adapter and declaration share a file — both are image-speed and want to be
  validated together. Policy cannot: it must be writable at 3am by someone
  without deploy access. This follows from I1 and is the argument for a
  separate control file rather than a `paused` key in `tina.toml`.
- Policy and admission can be built without touching the data-plane roles.
- The dispatcher stays stateless. It resolves policy, runs the query, and
  enqueues; it never stores a decision.
