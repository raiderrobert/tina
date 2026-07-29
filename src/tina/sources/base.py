"""The source adapter contract."""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, ValidationError

from tina.errors import TinaError
from tina.models import WorkItem


@runtime_checkable
class Source(Protocol):
    """A ticket tracker, seen through a query and a claim.

    Tina never inspects the content of a work item — it only knows the item
    matched a query. All judgment about what the item is happens in the track.
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
        raise SourceError(
            f"{source} source requires the {name} environment variable",
            fix=f"Set {name} in the worker environment.",
        )
    return value


def parse_payload[M: BaseModel](
    model: type[M], response: httpx.Response, source: str, path: str
) -> M:
    """Validate a tracker response against the shape Tina expects.

    The models only declare the fields Tina reads and tolerate unknown ones, so
    this fires when a tracker returns something genuinely different — not every
    time an API grows a field.
    """
    try:
        return model.model_validate(response.json())
    except ValueError as exc:
        detail = exc if isinstance(exc, ValidationError) else f"response was not JSON: {exc}"
        raise SourceError(f"{source}: unexpected response from {path}: {detail}") from None
