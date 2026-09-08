"""The server loop, driven over in-memory streams with a fake connector.

The same fake is what `tests/fakes/connector.py` runs as a process for the
client tests, so the two halves are tested against one implementation.
"""

from __future__ import annotations

import io
import json
from typing import Any

from tests.fakes.connector import FakeConnector

from tina.connector import protocol
from tina.connector.server import serve
from tina.models import WorkItem


def run(*messages: dict[str, Any]) -> tuple[list[dict[str, Any]], str, int]:
    stdin = io.StringIO("".join(protocol.encode(m) for m in messages))
    stdout, stderr = io.StringIO(), io.StringIO()
    code = serve(FakeConnector, stdin, stdout, stderr)
    return [json.loads(line) for line in stdout.getvalue().splitlines()], stderr.getvalue(), code


def init(**overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "protocol_version": 1,
        "client": {"name": "tina", "version": "test"},
        "track": "bug",
        "options": {"items": 2},
        "lifecycle": {"claim": "label", "claim_label": "taken", "blocked_label": "held"},
    }
    params.update(overrides)
    return protocol.request(1, "initialize", params)


def test_initialize_answers_with_version_identity_and_capabilities() -> None:
    answers, _, code = run(init(), protocol.notification("shutdown"))

    assert code == 0
    assert answers == [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocol_version": 1,
                "connector": {"name": "fake", "version": "9"},
                "capabilities": {"build_query": True, "verify_artifact": True},
            },
        }
    ]


def test_an_unsupported_version_is_refused_with_the_supported_list() -> None:
    (answer,), _, _ = run(init(protocol_version=2))

    assert answer["error"]["code"] == protocol.Code.UNSUPPORTED_VERSION
    assert answer["error"]["data"] == {"supported": [1]}


def test_bad_options_are_refused_with_a_fix() -> None:
    (answer,), _, _ = run(init(options={"items": "many"}))

    assert answer["error"]["code"] == protocol.Code.INVALID_OPTIONS
    assert "items" in answer["error"]["message"]
    assert answer["error"]["data"]["fix"]


def test_methods_before_initialize_are_refused() -> None:
    (answer,), _, _ = run(protocol.request(1, "query", {"query": "x"}))

    assert answer["error"]["code"] == protocol.Code.INVALID_REQUEST
    assert answer["error"]["message"] == "initialize first"


def test_every_method_round_trips_tinas_models() -> None:
    item = WorkItem(id="F-1", source="fake", title="one").model_dump(mode="json")
    answers, _, _ = run(
        init(),
        protocol.request(2, "login", {}),
        protocol.request(3, "query", {"query": "all"}),
        protocol.request(4, "get", {"id": "F-2"}),
        protocol.request(5, "matches", {"id": "F-1", "query": "all"}),
        protocol.request(6, "claim", {"item": item}),
        protocol.request(7, "claim_prognosis", {"item": item}),
        protocol.request(8, "claimed", {"query": "all"}),
        protocol.request(9, "annotate", {"item": item, "comment": "hi"}),
        protocol.request(10, "block", {"item": item}),
        protocol.request(11, "build_query", {"options": {}, "lifecycle": {}}),
        protocol.request(12, "verify_artifact", {"url": "https://fake.test/F-1"}),
        protocol.request(13, "verify_artifact", {"url": "https://elsewhere.test/x"}),
    )
    by_id = {a["id"]: a["result"] for a in answers}

    assert by_id[2] == {"identity": "fake-bot"}
    assert [i["id"] for i in by_id[3]["items"]] == ["F-1", "F-2"]
    assert by_id[4]["item"]["id"] == "F-2" and by_id[4]["item"]["source"] == "fake"
    assert by_id[5] == {"matches": True}
    assert by_id[6] == {"claimed": True}
    assert by_id[7] == {"would_claim": True, "holder": ""}
    assert by_id[8] == {"items": []}
    assert by_id[9] == {} and by_id[10] == {}
    assert by_id[11] == {"query": "fake:2 -label:held -label:taken"}
    assert by_id[12] == {"exists": True}
    assert by_id[13] is None


def test_a_tracker_failure_is_an_error_response_not_a_crash() -> None:
    answers, _, code = run(
        init(), protocol.request(2, "get", {"id": "F-broken"}), protocol.request(3, "login", {})
    )

    assert answers[1]["error"]["code"] == protocol.Code.SOURCE
    assert "tracker down" in answers[1]["error"]["message"]
    assert answers[1]["error"]["data"] == {"fix": "try later"}
    assert answers[2]["result"] == {"identity": "fake-bot"}, "the loop carried on"
    assert code == 0


def test_a_bug_in_the_connector_is_reported_with_the_traceback_on_stderr() -> None:
    answers, stderr, _ = run(init(), protocol.request(2, "get", {"id": "F-bug"}))

    assert answers[1]["error"]["code"] == protocol.Code.INTERNAL
    assert "RuntimeError" in answers[1]["error"]["message"]
    assert "Traceback" in stderr


def test_unknown_methods_and_bad_params() -> None:
    answers, _, _ = run(
        init(), protocol.request(2, "frobnicate", {}), protocol.request(3, "get", {"nope": 1})
    )

    assert answers[1]["error"]["code"] == protocol.Code.METHOD_NOT_FOUND
    assert answers[2]["error"]["code"] == protocol.Code.INVALID_PARAMS


def test_garbage_lines_get_a_parse_error_and_blank_lines_are_ignored() -> None:
    stdin = io.StringIO("not json\n\n" + protocol.encode(init()))
    stdout = io.StringIO()
    serve(FakeConnector, stdin, stdout, io.StringIO())
    answers = [json.loads(line) for line in stdout.getvalue().splitlines()]

    assert answers[0]["error"]["code"] == protocol.Code.PARSE
    assert answers[0]["id"] is None
    assert "result" in answers[1]


def test_end_of_input_ends_the_loop_cleanly() -> None:
    _, _, code = run(init())
    assert code == 0
