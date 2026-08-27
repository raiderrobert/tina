# 16. Dispatch budgets launches by live executor state

**Status:** Proposed

**Date:** 2026-08-27

## Context

`--limit` bounds launches per dispatch call, then forgets them. With a
15-minute scheduler and hour-long agent runs, every cycle launches the full
limit again: the real concurrency is limit × (worker duration / cycle), 4x to
12x the knob. And under `claim = "none"` nothing stops cycle N+1 from
launching a second worker for an item cycle N is still working — the query
still matches it. The tracker cannot answer "what is in flight" for such
tracks, because nothing was written to it; only the executor knows which
workers it started that have not finished.

## Decision

The `Executor` protocol grows `running(track) -> list[str]`: the item ids of
this track's workers still in flight, one entry per worker, with the stable
sweep marker standing in for an item-less worker. The dispatch budget becomes
`max(0, min(--limit, policy) − len(running))`, and an item whose id is already
running is skipped, not re-enqueued.

The execution list is queried fresh every cycle and never stored. This keeps
I4 ([ADR-011](011-control-plane-data-plane-split.md)) intact by reading the
second ledger that already exists — the executor's own execution list — rather
than creating one. `local` returns an empty list honestly: its workers are
synchronous subprocesses, so none can be in flight while dispatch runs.
`cloudrun` lists the job's non-terminal executions, reads the item id back
from the args overrides `enqueue` set, and stops paging at a fixed horizon —
workers have finite timeouts, so the deep tail is all terminal in practice.

## Consequences

- Effective concurrency per track is now bounded by the knob rather than by
  the knob times the duration-to-cycle ratio, and `claim = "none"` tracks get
  the dedupe the query cannot give them.
- The dispatcher stays stateless; a dispatcher that dies mid-loop still
  leaves nothing stuck, and the next cycle recomputes the budget from scratch.
- A dry run builds no executor, so it assumes zero workers in flight and says
  so — the same discipline as its policy reporting.
- The horizon trades completeness for bounded paging: a worker that outlives
  it stops being counted. That worker has also outlived any sane timeout, so
  the miscount is the smaller problem.
