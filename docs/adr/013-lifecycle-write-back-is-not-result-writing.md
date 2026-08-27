# 13. Lifecycle write-back is not result writing

**Status:** Proposed

**Date:** 2026-08-27

## Context

ADR-001 rules that Tina does orchestration only and never writes a result. A
failed run currently leaves nothing on the work item, so the next dispatch
matches it again, and again — a poison-pill item is retried every cycle while
looking like normal activity in the logs. Fixing that means Tina writes a
failure comment and an exclusion marker back to the work item, which sits
close enough to ADR-001's line to need an explicit ruling rather than arriving
quietly inside a feature.

## Decision

Two categories of write, with the line drawn between them:

- **Result** — the thing the work item asked for: a PR, a document, a config
  change. Always the agent's, via its own tools. Unchanged.
- **Lifecycle** — the tracker's record of who holds the item and what became
  of the attempt. Already Tina's, via `claim`. Extended to `annotate` (a
  comment about the run) and `block` (the source's exclusion marker, so the
  configured query stops matching the item).

Lifecycle write-back is Tina's job. Result writing remains the agent's.

## Consequences

- `Source` grows write operations beyond `claim`: `annotate` and `block`. The
  adapter contract is still the boundary; new result systems require no Tina
  code.
- Tina still never inspects work item content (invariant I3 of
  [011](011-control-plane-data-plane-split.md)): it writes a status and a
  link, never a judgment about the item.
- Lifecycle writes are best-effort. A reporting hiccup must not mask the
  failure it reports, so `annotate` and `block` log failures and never raise.
- A track opts out with `on_failure = "leave"`, so a deployment that wants
  strict orchestration-only keeps it.
