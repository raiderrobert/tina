"""The executor contract."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Executor(Protocol):
    """How the dispatcher enqueues workers. One item = one worker."""

    def enqueue(self, workflow: str, item_id: str) -> None:
        """Start a `tina run --workflow <workflow> --item <item_id>` worker."""
        ...


class ExecutorError(RuntimeError):
    """An executor could not enqueue a worker."""
