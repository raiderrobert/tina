"""The source adapter contract."""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

from tina.errors import TinaError
from tina.models import WorkItem


@runtime_checkable
class Source(Protocol):
    """A ticket tracker, seen through a query and a claim.

    Tina never inspects the content of a work item — it only knows the item
    matched a query. All judgment about what the item is happens in the activity.
    """

    def query(self, q: str) -> list[WorkItem]:
        """Run the configured query and return matching items."""
        ...

    def get(self, item_id: str) -> WorkItem:
        """Fetch a single item by tracker identifier."""
        ...

    def claim(self, item: WorkItem) -> bool:
        """Take ownership of an item.

        True means this worker now owns it. False means somebody else does and
        the worker should exit `no_action_needed`.
        """
        ...


class SourceError(TinaError, RuntimeError):
    """A source adapter could not talk to its tracker."""


def require_env(name: str, source: str) -> str:
    """Read a required environment variable or fail with a usable message."""
    value = os.environ.get(name)
    if not value:
        raise SourceError(f"{source} source requires the {name} environment variable")
    return value
