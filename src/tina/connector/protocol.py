"""The connector protocol's wire shapes — docs/connector-protocol.md, as code.

JSON-RPC 2.0, one message per line, UTF-8, no embedded newlines. This module
is the only place the framing and the error codes are spelled out; the client
(`tina.connector.client`) and the server loop (`tina.connector.server`) both
import it, so the two sides cannot drift.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tina.errors import TinaError

#: The version of docs/connector-protocol.md this code speaks. Increments only
#: for an incompatible change: a method removed or a shape changed.
PROTOCOL_VERSION = 1

#: Every request a connector must answer, in the order a run makes them.
METHODS = (
    "initialize",
    "login",
    "query",
    "get",
    "matches",
    "claim",
    "claim_prognosis",
    "claimed",
    "annotate",
    "block",
)
#: Declared in `initialize`'s capabilities and called only when declared.
OPTIONAL_METHODS = ("build_query", "verify_artifact")
#: A notification: no id, no response.
SHUTDOWN = "shutdown"

#: Methods a read-only invocation (`--dry-run`, `status`, `validate`,
#: `doctor`) is allowed to send. A connector may enforce this.
READ_ONLY = frozenset(
    {"initialize", "login", "query", "get", "matches", "claim_prognosis", "claimed"}
    | set(OPTIONAL_METHODS)
    | {SHUTDOWN}
)


class Code:
    """Error codes. The -3200x range is the protocol's; the rest is JSON-RPC's."""

    UNSUPPORTED_VERSION = -32001
    INVALID_OPTIONS = -32002
    SOURCE = -32003
    NOT_FOUND = -32004
    PARSE = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL = -32603


class ProtocolError(TinaError, RuntimeError):
    """The other side broke the protocol: a non-JSON line, a response to
    nothing, a missing field. Distinct from a tracker failure, which is a
    well-formed error response."""


class Lifecycle(BaseModel):
    """The track keys Tina owns and a connector needs to claim and block."""

    model_config = ConfigDict(frozen=True)

    claim: Literal["assign", "label", "none"] = "assign"
    claim_label: str | None = None
    blocked_label: str = "tina-blocked"


class InitializeParams(BaseModel):
    protocol_version: int
    client: dict[str, str] = Field(default_factory=dict)
    track: str
    options: dict[str, Any] = Field(default_factory=dict)
    lifecycle: Lifecycle = Field(default_factory=Lifecycle)


class Capabilities(BaseModel):
    build_query: bool = False
    verify_artifact: bool = False


class InitializeResult(BaseModel):
    protocol_version: int
    connector: dict[str, str] = Field(default_factory=dict)
    capabilities: Capabilities = Field(default_factory=Capabilities)


def encode(message: dict[str, Any]) -> str:
    """One message as one line. `ensure_ascii` off keeps titles readable; the
    default separators keep newlines out of the payload."""
    return json.dumps(message, ensure_ascii=False) + "\n"


def decode(line: str) -> dict[str, Any]:
    try:
        message = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"not a JSON-RPC message: {line[:120]!r} ({exc})") from None
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        raise ProtocolError(f"not a JSON-RPC 2.0 message: {line[:120]!r}")
    return message


def request(id: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}}


def notification(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params or {}}


def result(id: Any, value: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id, "result": value}


def error(id: Any, code: int, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message}
    if data:
        body["data"] = data
    return {"jsonrpc": "2.0", "id": id, "error": body}
