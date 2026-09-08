"""The governor seam: a throughput policy consulted once per dispatch cycle.

The control file says how many workers a human allows. A governor says how
many the factory should run *right now*, below that: it reads signals Tina
does not model — worker failure rates, spend, time of day — and lowers the
cap when they are bad. Tina ships none. A deployment that has one hands it to
`dispatch_track`, which asks it twice per cycle: `cap` before picking work,
`record` after, with everything the cycle did. The human ceiling always
dominates: a governor can only lower it (ADR-011 — policy is a gate, and the
gate is read at dispatch and nowhere else).

Both calls are best-effort from the dispatcher's point of view: a governor
that raises is logged and treated as absent for the cycle, so a broken
feedback loop degrades to the static cap rather than stopping dispatch.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Governor(Protocol):
    """Lower the cap on a cycle, and learn from what the cycle did."""

    def cap(self, track: str, ceiling: int, in_flight: int) -> int | None:
        """A cap on live workers for this cycle, or None to leave the ceiling.

        Called after the control file and the in-flight count are known,
        before any work is picked. A value above `ceiling` is clamped to it.
        """
        ...

    def record(
        self,
        track: str,
        *,
        ceiling: int,
        cap: int,
        in_flight: int,
        budget: int,
        matched: int,
        launched: int,
    ) -> None:
        """Observe the cycle: the ceiling, the cap that applied, how many
        workers were live, how many launches the budget allowed, how many
        items the query matched, and how many were launched. Called once,
        after the last enqueue."""
        ...
