"""A connector with canned answers: the one implementation both halves are tested against.

`tests/unit/test_connector_server.py` drives it in-process over StringIO;
`tests/unit/test_connector_client.py` runs it as a subprocess
(`python -m tests.fakes.connector`), which is exactly how Tina runs a real one.
"""

from __future__ import annotations

import sys
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from tina.connector.protocol import Lifecycle
from tina.connector.server import Connector, OptionsError, serve
from tina.models import WorkItem
from tina.sources.base import ClaimPrognosis, SourceError


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: int = 1
    # Makes `login` hang, for the client's timeout test.
    sleep_on_login: float = 0.0


class FakeConnector(Connector):
    name = "fake"
    version = "9"

    def __init__(self, options: dict[str, Any], lifecycle: Lifecycle) -> None:
        super().__init__(options, lifecycle)
        try:
            self.opts = Options.model_validate(options)
        except ValidationError as exc:
            raise OptionsError(
                str(exc.errors()[0]["loc"][0]) + ": " + exc.errors()[0]["msg"], fix="see Options"
            ) from None
        self.calls: list[str] = []

    def _item(self, n: int) -> WorkItem:
        return WorkItem(
            id=f"F-{n}", source=self.name, title=f"item {n}", url=f"https://fake.test/F-{n}"
        )

    def login(self) -> str:
        if self.opts.sleep_on_login:
            import time

            time.sleep(self.opts.sleep_on_login)
        return "fake-bot"

    def query(self, q: str) -> list[WorkItem]:
        return [self._item(n) for n in range(1, self.opts.items + 1)]

    def get(self, item_id: str) -> WorkItem:
        if item_id == "F-broken":
            raise SourceError("fake: tracker down", fix="try later")
        if item_id == "F-bug":
            raise RuntimeError("a bug in the connector")
        return self._item(int(item_id.split("-")[1]))

    def matches(self, item_id: str, q: str) -> bool:
        return True

    def claim(self, item: WorkItem) -> bool:
        return True

    def claim_prognosis(self, item: WorkItem) -> ClaimPrognosis:
        return ClaimPrognosis(would_claim=True, holder="")

    def claimed(self, q: str) -> list[WorkItem]:
        return []

    def annotate(self, item: WorkItem, comment: str) -> None:
        print(f"annotated {item.id}: {comment}", file=sys.stderr)

    def block(self, item: WorkItem) -> None:
        print(f"blocked {item.id}", file=sys.stderr)

    def build_query(self) -> str:
        parts = [f"fake:{self.opts.items}", f"-label:{self.lifecycle.blocked_label}"]
        if self.lifecycle.claim == "label" and self.lifecycle.claim_label:
            parts.append(f"-label:{self.lifecycle.claim_label}")
        return " ".join(parts)

    def verify_artifact(self, url: str) -> bool | None:
        if not url.startswith("https://fake.test/"):
            return None
        return not url.endswith("/missing")


if __name__ == "__main__":
    sys.exit(serve(FakeConnector))
