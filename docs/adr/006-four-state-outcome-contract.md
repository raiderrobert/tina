# 6. Four-state outcome contract

**Status:** Accepted

**Date:** 2026-07-28

## Context

The worker needs to know what to do after an agent run. Inputs to a run can be
constrained, but outputs resist a rich contract — real runs terminate in wildly
varying ways, and every attempt to model the variety produces a type hierarchy that
the next unanticipated failure breaks. The useful question is not what happened but
what the factory should do next.

## Decision

`outcome.json` models the next action, with four terminal states:

```json
{
  "outcome": "resolved | no_action_needed | needs_human | failed",
  "details": "free-form prose — unmodeled, as much as it wants",
  "artifacts": [{ "kind": "github:pr", "url": "..." }]
}
```

`details` stays prose. `artifacts` is optional and consumed by
[007](007-generic-artifact-verification.md).

## Consequences

- Permission failures, tool errors, and unhandled exceptions are all `failed` plus
  a prose string. That case never needed a rich type; it needed somewhere to put
  text.
- `needs_human` separates "this run broke" from "this run correctly concluded a
  person must decide." The infra branch of the vulnerability track lives there
  permanently, and it is not an error.
- `no_action_needed` is the exit for a losing claim race
  ([004](004-worker-side-claiming.md)) as well as for an item that turned out to
  need nothing.
- Only the enum is machine-readable. Anything wanting structured analytics over
  outcomes has to parse prose or wait for a richer contract driven by observed
  need.
