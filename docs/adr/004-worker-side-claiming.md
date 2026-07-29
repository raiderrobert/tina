# 4. Worker-side claiming; the tracker is the ledger

**Status:** Accepted

**Date:** 2026-07-28

## Context

Something must prevent two runs from working the same item. That state could live
in a Tina-owned database, or in the tracker that already has an assignee field and
already excludes assigned items from the query. Given the tracker, the remaining
question is who writes the claim: the dispatcher before enqueueing, or the worker
on start.

## Decision

The tracker is the ledger. Tina holds no persistent state — no database, no local
state file.

**The worker claims, not the dispatcher.** Claiming assigns the item to the bot
user or applies a label, and the configured query excludes claimed items (what
`assignee IS EMPTY` already does). Worker start is `claim() → if already claimed,
exit no_action_needed`.

This is a choice between failure modes:

| | Failure mode |
|---|---|
| Worker claims | dispatch can enqueue an item twice; losers exit `no_action_needed`. Self-healing. |
| Dispatcher claims | no duplicates, but a dispatcher dying mid-loop leaves items claimed and unworked, needing a sweeper. |

Self-healing wins. Duplicate containers that exit in seconds are cheap; stuck items
need human recovery.

## Consequences

- Containers are restartable and multiple invokers are safe by default.
- Duplicate enqueues are a tolerated, cheap no-op rather than a bug to prevent.
- The claim must be atomic enough per tracker to not double-run. Jira assignment is
  a real compare-and-set when conditioned on the assignee being empty. GitHub's
  assign API is an idempotent add with no conditional, so GitHub claiming is
  assign-then-reread and exit `no_action_needed` if not the sole assignee.
- A small race window remains on GitHub. It is acceptable because duplicate workers
  are already the tolerated failure mode.
- A stuck-claim sweeper is deferred; worker-side claiming is what makes it
  unnecessary in v1.
