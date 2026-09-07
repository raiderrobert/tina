# 17. A governor seam, and no governor

**Status:** Proposed

**Date:** 2026-09-03

## Context

A factory that has run unattended for a while grows an adaptive throughput
policy: halve the cap on a failure burst, climb it back on healthy deep-backlog
cycles, remember the level that last hurt. The signals it reads — worker
failure rates from the executor's metrics, spend, time of day — are things Tina
deliberately does not model, and the policy itself is opinion that a
deployment tunes for months. Building one into Tina would either fix that
opinion or grow a policy language. Leaving it out entirely forces a deployment
to fork `dispatch_track` to plug its own in, and the fork drifts.

## Decision

`dispatch_track` accepts an optional `Governor`: a protocol with two methods.
`cap(track, ceiling, in_flight)` is asked once per cycle, after the control
file and the in-flight count are known and before any work is picked, and may
return a lower cap. `record(track, ceiling=, cap=, in_flight=, budget=,
matched=, launched=)` is told what the cycle did, once, after the last
enqueue. Tina ships no governor. The human ceiling always dominates: a cap
above it is clamped, and a governor that raises is logged and treated as
absent for the cycle, so a broken feedback loop degrades to the static cap.

## Consequences

- Adaptive throughput is a deployment concern with a stable seam, not a fork.
  A governor lives beside the factory's other infrastructure-specific code —
  its metrics client, its state bucket — and Tina never learns what those are.
- The seam is consistent with I1 and I2 of
  [ADR-011](011-control-plane-data-plane-split.md): a governor is consulted at
  dispatch and nowhere else, and it can only lower the gate a human set.
- The dispatch record carries the ceiling and the cap separately and names
  `governor` as the origin when it bound, so a cycle throttled by the governor
  is distinguishable from one throttled by a human in the log alone.
- Two calls per cycle are the whole surface. A governor wanting more — the
  matched items themselves, say — would be reading work-item content at the
  control plane, which I3 forbids.
