# 15. Sweep tracks: a scheduled run with no work item

**Status:** Proposed

**Date:** 2026-08-27

## Context

Every path through `dispatch` and `run` starts at a source query, so a track
must have a queue. Some genuinely useful tracks do not: scan the last N job
executions and file an issue per recurring failure signature, sweep for stuck
claims, produce a periodic report. In all of them, discovering the work,
deduplicating it against what was already filed, and delivering it is the
skill's job — exactly the division of labor Tina already argues for, minus the
query. Under the current model those tracks are inexpressible.

## Decision

`mode = "queue" | "sweep"` per track, defaulting to `"queue"`. A sweep track's
`dispatch` enqueues exactly one worker with no item; `--limit` does not apply.
`run` skips the source, the claim, and the eligibility re-check entirely;
prompt assembly omits the work-item block; the outcome contract is unchanged.
The run record carries a stable `sweep` marker where the item id would be.

Queue keys — `source`, `query`, `repo`, `claim`, `claim_label`,
`claim_transition`, `on_failure`, `blocked_label` — are invalid on a sweep
entry and rejected at config load, each named. The control file still gates
dispatch: paused wins, and a sweep launch counts as one worker against
`max_concurrency`.

## Consequences

- The source → item → run chain becomes optional at its head. Everything from
  the prompt down is shared, so the outcome contract, verification, and the
  run record keep their shape.
- Deduplication moves entirely into the skill. Nothing but `max_concurrency`
  and the external schedule stops two overlapping sweep launches, which is the
  same trade `claim = "none"` already made.
- Lifecycle write-back and the eligibility re-check have no item to act on;
  rejecting their keys at load is what keeps a sweep entry honest.
- The stuck-claim sweeper deferred in architecture §18 becomes expressible as
  a track, not Tina code.
