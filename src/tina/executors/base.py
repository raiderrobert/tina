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


class ExecutorError(TinaError, RuntimeError):
    """An executor could not enqueue a worker."""
