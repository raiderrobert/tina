from __future__ import annotations

import json

import pytest

from tina.connector import protocol


def test_one_message_is_one_line() -> None:
    line = protocol.encode(protocol.request(1, "query", {"query": "a\nb"}))

    assert line.endswith("\n")
    assert line.count("\n") == 1, "a newline inside a value is escaped, never literal"
    assert json.loads(line) == {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "query",
        "params": {"query": "a\nb"},
    }


def test_decode_insists_on_json_rpc_2() -> None:
    assert protocol.decode('{"jsonrpc": "2.0", "id": 1, "result": {}}')["id"] == 1
    with pytest.raises(protocol.ProtocolError, match="not a JSON-RPC message"):
        protocol.decode("hello\n")
    with pytest.raises(protocol.ProtocolError, match="not a JSON-RPC 2.0"):
        protocol.decode('{"id": 1}')
    with pytest.raises(protocol.ProtocolError):
        protocol.decode("[1, 2]")


def test_notifications_carry_no_id() -> None:
    assert "id" not in protocol.notification(protocol.SHUTDOWN)


def test_error_shape() -> None:
    err = protocol.error(
        3, protocol.Code.INVALID_OPTIONS, "repo: Field required", {"fix": "Set repo"}
    )

    assert err == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {"code": -32002, "message": "repo: Field required", "data": {"fix": "Set repo"}},
    }
    assert "data" not in protocol.error(3, protocol.Code.SOURCE, "x")["error"]


def test_the_read_only_set_excludes_every_write() -> None:
    assert {"claim", "annotate", "block"}.isdisjoint(protocol.READ_ONLY)
    assert {"query", "get", "matches", "claim_prognosis", "claimed", "login"} <= protocol.READ_ONLY
