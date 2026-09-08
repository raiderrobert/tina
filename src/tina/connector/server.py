"""The server half of the connector protocol, for connectors written in Python.

A connector author in another language implements docs/connector-protocol.md
directly: read a line, dispatch, write a line. A Python author subclasses
`Connector`, implements the `Source` contract, and calls `serve()` from a
console script — the loop, the framing, the handshake, and the error mapping
are this module's. Tina's own connectors are built exactly this way, so this
is also the reference implementation of the server side.
"""

from __future__ import annotations

import sys
import traceback
from typing import Any, ClassVar, TextIO

from pydantic import ValidationError

from tina.connector import protocol
from tina.connector.protocol import Capabilities, Code, InitializeParams, Lifecycle
from tina.models import WorkItem
from tina.sources.base import ClaimPrognosis, SourceError


class OptionsError(Exception):
    """The `[<track>.options]` table is not one this connector accepts.

    Raised from `Connector.__init__`; answered as error -32002 with `fix`, which
    is what lets `tina validate` and `tina doctor` catch a bad table at load.
    """

    def __init__(self, message: str, fix: str = "") -> None:
        super().__init__(message)
        self.fix = fix


class Connector:
    """What a Python connector implements.

    The nine `Source` methods, taking and returning Tina's models, plus two
    optional ones. Construction receives the track's options — validate them
    here and raise `OptionsError` — and the lifecycle settings Tina owns.
    """

    name: ClassVar[str] = "connector"
    version: ClassVar[str] = "0"

    def __init__(self, options: dict[str, Any], lifecycle: Lifecycle) -> None:
        self.options = options
        self.lifecycle = lifecycle

    # -- the Source contract --------------------------------------------------

    def login(self) -> str:
        raise NotImplementedError

    def query(self, q: str) -> list[WorkItem]:
        raise NotImplementedError

    def get(self, item_id: str) -> WorkItem:
        raise NotImplementedError

    def matches(self, item_id: str, q: str) -> bool:
        raise NotImplementedError

    def claim(self, item: WorkItem) -> bool:
        raise NotImplementedError

    def claim_prognosis(self, item: WorkItem) -> ClaimPrognosis:
        raise NotImplementedError

    def claimed(self, q: str) -> list[WorkItem]:
        raise NotImplementedError

    def annotate(self, item: WorkItem, comment: str) -> None:
        raise NotImplementedError

    def block(self, item: WorkItem) -> None:
        raise NotImplementedError

    # -- optional capabilities: define to declare them --------------------------

    build_query: Any = None
    verify_artifact: Any = None

    @classmethod
    def capabilities(cls) -> Capabilities:
        return Capabilities(
            build_query=callable(cls.build_query),
            verify_artifact=callable(cls.verify_artifact),
        )


def serve(
    connector_cls: type[Connector],
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the protocol loop until `shutdown` or end of input. Returns an exit code.

    Every request gets exactly one response. A tracker failure is an error
    response, not a crash; an unexpected exception is `-32603` with the
    traceback on stderr, and the loop continues — the connector's failure
    should surface as a well-formed message, not a dead pipe.
    """
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    session = _Session(connector_cls, stderr)

    for line in stdin:
        if not line.strip():
            continue
        try:
            message = protocol.decode(line)
        except protocol.ProtocolError as exc:
            _write(stdout, protocol.error(None, Code.PARSE, str(exc)))
            continue
        method = message.get("method")
        if method == protocol.SHUTDOWN:
            return 0
        if "id" not in message or not isinstance(method, str):
            _write(stdout, protocol.error(None, Code.INVALID_REQUEST, "expected a request"))
            continue
        _write(stdout, session.handle(message["id"], method, message.get("params") or {}))
    return 0


class _Session:
    """One connector instance's worth of dispatch: unset until `initialize`."""

    def __init__(self, connector_cls: type[Connector], stderr: TextIO) -> None:
        self.connector_cls = connector_cls
        self.connector: Connector | None = None
        self.stderr = stderr

    def handle(self, id: Any, method: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            if method == "initialize":
                return protocol.result(id, self._initialize(params))
            if self.connector is None:
                return protocol.error(id, Code.INVALID_REQUEST, "initialize first")
            handler = _HANDLERS.get(method)
            if handler is None:
                return protocol.error(id, Code.METHOD_NOT_FOUND, f"unknown method {method!r}")
            return protocol.result(id, handler(self.connector, params))
        except _Unsupported as exc:
            return protocol.error(
                id,
                Code.UNSUPPORTED_VERSION,
                f"protocol version {exc.requested} is not supported",
                {"supported": [protocol.PROTOCOL_VERSION]},
            )
        except OptionsError as exc:
            return protocol.error(id, Code.INVALID_OPTIONS, str(exc), {"fix": exc.fix})
        except SourceError as exc:
            return protocol.error(id, Code.SOURCE, str(exc), {"fix": exc.fix} if exc.fix else None)
        except (KeyError, TypeError, ValidationError) as exc:
            return protocol.error(id, Code.INVALID_PARAMS, f"{method}: bad params: {exc}")
        except Exception as exc:  # the connector's bug, reported rather than fatal
            self.stderr.write(traceback.format_exc())
            self.stderr.flush()
            return protocol.error(id, Code.INTERNAL, f"{method}: {type(exc).__name__}: {exc}")

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        parsed = InitializeParams.model_validate(params)
        if parsed.protocol_version != protocol.PROTOCOL_VERSION:
            raise _Unsupported(parsed.protocol_version)
        self.connector = self.connector_cls(parsed.options, parsed.lifecycle)
        return protocol.InitializeResult(
            protocol_version=protocol.PROTOCOL_VERSION,
            connector={"name": self.connector_cls.name, "version": self.connector_cls.version},
            capabilities=self.connector_cls.capabilities(),
        ).model_dump()


class _Unsupported(Exception):
    def __init__(self, requested: int) -> None:
        super().__init__(requested)
        self.requested = requested


def _items(items: list[WorkItem]) -> dict[str, Any]:
    return {"items": [item.model_dump(mode="json") for item in items]}


def _item(params: dict[str, Any]) -> WorkItem:
    return WorkItem.model_validate(params["item"])


_HANDLERS = {
    "login": lambda c, p: {"identity": c.login()},
    "query": lambda c, p: _items(c.query(p["query"])),
    "get": lambda c, p: {"item": c.get(p["id"]).model_dump(mode="json")},
    "matches": lambda c, p: {"matches": c.matches(p["id"], p["query"])},
    "claim": lambda c, p: {"claimed": c.claim(_item(p))},
    "claim_prognosis": lambda c, p: c.claim_prognosis(_item(p)).model_dump(),
    "claimed": lambda c, p: _items(c.claimed(p["query"])),
    "annotate": lambda c, p: (c.annotate(_item(p), p["comment"]), {})[1],
    "block": lambda c, p: (c.block(_item(p)), {})[1],
    "build_query": lambda c, p: {"query": c.build_query()},
    "verify_artifact": lambda c, p: _exists(c.verify_artifact(p["url"])),
}


def _exists(answer: bool | None) -> dict[str, Any] | None:
    return None if answer is None else {"exists": bool(answer)}


def _write(stdout: TextIO, message: dict[str, Any]) -> None:
    stdout.write(protocol.encode(message))
    stdout.flush()
