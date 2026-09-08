"""The client half, against the fake connector running as a real subprocess.

`tests/fakes/connector.py` is served with `python -m`, from the repo root, the
way Tina runs any connector: a command, stdin, stdout, stderr.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tina.connector.client import ConnectorClient, OptionsRejected
from tina.connector.protocol import Lifecycle
from tina.models import WorkItem
from tina.sources.base import Source, SourceError

ROOT = Path(__file__).resolve().parents[2]
COMMAND = [sys.executable, "-m", "tests.fakes.connector"]


def client(options: dict[str, Any] | None = None, **kwargs: Any) -> ConnectorClient:
    return ConnectorClient(
        COMMAND,
        name="fake",
        track="bug",
        options={"items": 2} if options is None else options,
        lifecycle=Lifecycle(claim="label", claim_label="taken", blocked_label="held"),
        env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"},
        **kwargs,
    )


@pytest.fixture
def fake() -> Iterator[ConnectorClient]:
    c = client()
    yield c
    c.close()


def test_the_client_is_a_source() -> None:
    assert isinstance(client(), Source)


def test_the_handshake_happens_on_first_use_and_reports_who_answered(fake: ConnectorClient) -> None:
    assert fake.connector == {"name": "fake", "version": "9"}
    assert fake.capabilities.build_query and fake.capabilities.verify_artifact


def test_every_source_method_round_trips(fake: ConnectorClient) -> None:
    items = fake.query("all")
    assert [i.id for i in items] == ["F-1", "F-2"]
    assert isinstance(items[0], WorkItem) and str(items[0].url) == "https://fake.test/F-1"
    assert fake.get("F-2").title == "item 2"
    assert fake.matches("F-1", "all") is True
    assert fake.claim(items[0]) is True
    prognosis = fake.claim_prognosis(items[0])
    assert (prognosis.would_claim, prognosis.holder) == (True, "")
    assert fake.claimed("all") == []
    fake.annotate(items[0], "hello")
    fake.block(items[0])
    assert fake.login() == "fake-bot"


def test_capabilities_answer_through_the_pipe(fake: ConnectorClient) -> None:
    assert fake.build_query() == "fake:2 -label:held -label:taken"
    assert fake.verify_artifact("https://fake.test/F-1") is True
    assert fake.verify_artifact("https://fake.test/missing") is False
    assert fake.verify_artifact("https://elsewhere.test/x") is None


def test_a_tracker_failure_arrives_as_a_source_error_with_its_fix(fake: ConnectorClient) -> None:
    with pytest.raises(SourceError, match="tracker down") as excinfo:
        fake.get("F-broken")
    assert excinfo.value.fix == "try later"


def test_the_connectors_stderr_is_relayed_and_kept(
    fake: ConnectorClient, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.block(WorkItem(id="F-1", source="fake"))
    fake.close()  # flushes the relay thread by ending the process

    assert "[fake] blocked F-1" in capsys.readouterr().err


def test_rejected_options_are_a_config_error_at_first_use() -> None:
    c = client(options={"items": "many"})
    with pytest.raises(OptionsRejected, match=r"\[bug\.options\]: items") as excinfo:
        c.login()
    assert excinfo.value.fix == "see Options"


def test_an_unsupported_protocol_version_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tina claims to speak a version the connector does not; the refusal names both."""
    from tina.connector import protocol

    monkeypatch.setattr(protocol, "PROTOCOL_VERSION", 99)
    c = client()
    with pytest.raises(SourceError, match=r"does not speak protocol version 99 \(supports \[1\]\)"):
        c.login()
    c.close()


def test_a_missing_command_is_a_source_error_with_a_fix() -> None:
    c = ConnectorClient(
        ["tina-source-does-not-exist"], name="ghost", track="t", options={}, lifecycle=Lifecycle()
    )
    with pytest.raises(SourceError, match="could not start connector") as excinfo:
        c.login()
    assert "[sources.ghost] command" in excinfo.value.fix


def test_a_silent_connector_times_out_and_is_killed() -> None:
    c = client(options={"items": 1, "sleep_on_login": 30}, timeout=0.5)
    with pytest.raises(SourceError, match="gave no answer within 0.5s during login"):
        c.login()
    assert c._process is None, "killed and forgotten; the next call would respawn"


def test_a_connector_that_dies_is_reported_with_its_stderr() -> None:
    dying = [
        sys.executable,
        "-c",
        "import sys; sys.stderr.write('boom: no config\\n'); sys.exit(3)",
    ]
    c = ConnectorClient(dying, name="dying", track="t", options={}, lifecycle=Lifecycle())
    with pytest.raises(SourceError) as excinfo:
        c.login()
    message = str(excinfo.value)
    assert "exited 3 during initialize" in message
    assert "boom: no config" in message


def test_close_sends_shutdown_and_the_process_exits_zero(fake: ConnectorClient) -> None:
    fake.login()
    process = fake._process
    assert process is not None
    fake.close()
    assert process.returncode == 0
    fake.close()  # idempotent


def test_the_fake_runs_from_the_repo_root_as_tina_would_run_it() -> None:
    """The command Tina would put in [sources.fake]: `python -m tests.fakes.connector`."""
    hello = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocol_version": 1, "track": "t"},
    }
    completed = subprocess.run(
        COMMAND,
        input=json.dumps(hello) + "\n",
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=30,
    )
    assert completed.returncode == 0
    assert '"connector": {"name": "fake", "version": "9"}' in completed.stdout
