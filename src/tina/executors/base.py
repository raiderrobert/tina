"""The executor contract."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from tina.errors import TinaError


@runtime_checkable
class Executor(Protocol):
    """How the dispatcher enqueues workers. One item = one worker."""

    def enqueue(self, track: str, item_id: str) -> None:
        """Start a `tina run --track <track> --item <item_id>` worker."""
        ...

    def run_url(self) -> str | None:
        """A deep link to the logs of the worker calling this, or None.

        Answered from the environment `tina run` is already inside, not from
        anything decided at enqueue time. None when the environment cannot
        tell — the caller omits the link rather than inventing one.
        """
        ...


class ExecutorError(TinaError, RuntimeError):
    """An executor could not enqueue a worker."""
