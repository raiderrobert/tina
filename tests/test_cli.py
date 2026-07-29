from __future__ import annotations

import io
import json
import logging
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from tina import cli, config, log, verify
from tina.models import OutcomeReport, OutcomeStatus, WorkItem

# The one URL the stubbed verifier considers real.
GOOD_ARTIFACT = "https://example.test/pr/1"
MISSING_ARTIFACT = "https://example.test/pr/999"

CONFIG = """
harness = "fake"
activities_dir = "activities"

[harnesses.fake]
command = ["{python}", "{script}", "{{prompt_file}}", "{{outcome_dir}}"]

[vul]
source = "jira"
query = "project = VUL"
activity = "remediate"
result = "github:pr"
"""

# A fake agent: reads the prompt, writes the outcome it was told to write.
AGENT = """\
import pathlib, sys
prompt = pathlib.Path(sys.argv[1]).read_text()
outcome = pathlib.Path(__file__).with_name("outcome_to_write.json").read_text()
assert "Remediate the vulnerability" in prompt, "activity skill missing from prompt"
assert "VUL-1" in prompt, "work item missing from prompt"
pathlib.Path(sys.argv[2], "outcome.json").write_text(outcome)
"""


class FakeSource:
    """Stands in for a tracker. Records what the CLI asked it to do."""

    def __init__(self, items: list[WorkItem], claimable: bool = True) -> None:
        self.items = items
        self.claimable = claimable
        self.claimed: list[str] = []

    def query(self, q: str) -> list[WorkItem]:
        self.queried = q
        return list(self.items)

    def get(self, item_id: str) -> WorkItem:
        return next(item for item in self.items if item.id == item_id)

    def claim(self, item: WorkItem) -> bool:
        self.claimed.append(item.id)
        return self.claimable


class FakeExecutor:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, str]] = []

    def enqueue(self, workflow: str, item_id: str) -> None:
        self.enqueued.append((workflow, item_id))


@pytest.fixture
def records() -> Iterator[io.StringIO]:
    """The JSON log lines `dispatch`/`run` emit, collected in memory.

    `cli.main` calls `log.configure()` itself; the entrypoint functions it calls
    do not, so tests that skip `main` install the same formatter here.
    """
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(log.JSONFormatter())
    root = logging.getLogger()
    previous, previous_level = root.handlers[:], root.level
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    yield stream
    root.handlers, root.level = previous, previous_level


@pytest.fixture(autouse=True)
def offline_verify(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the real verifier over a mock transport instead of the network."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200 if str(request.url) == GOOD_ARTIFACT else 404)

    real = verify.verify

    def offline(report: OutcomeReport, client: httpx.Client | None = None) -> OutcomeReport:
        return real(report, httpx.Client(transport=httpx.MockTransport(handler)))

    monkeypatch.setattr(verify, "verify", offline)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A config, an activity skill, and a fake harness on disk."""
    script = tmp_path / "agent.py"
    script.write_text(AGENT)
    (script.parent / "outcome_to_write.json").write_text(
        json.dumps({"outcome": "no_action_needed", "details": "nothing to do"})
    )

    activity = tmp_path / "activities" / "remediate"
    activity.mkdir(parents=True)
    (activity / "SKILL.md").write_text("# Remediate the vulnerability\n")

    path = tmp_path / "tina.toml"
    path.write_text(CONFIG.format(python=sys.executable, script=script))
    return path


def outcome_written(project: Path, payload: dict[str, Any]) -> None:
    (project.parent / "outcome_to_write.json").write_text(json.dumps(payload))


def last_record(output: str) -> dict[str, Any]:
    """The final JSON line: the RunRecord, or the error that stopped the run."""
    lines = [line for line in output.splitlines() if line.startswith("{")]
    assert lines, "expected at least one JSON log line"
    return json.loads(lines[-1])


def items(*ids: str) -> list[WorkItem]:
    return [WorkItem(id=item_id, source="jira", title=item_id) for item_id in ids]


def test_parser_defaults() -> None:
    args = cli.build_parser().parse_args(["dispatch", "--workflow", "vul"])

    assert (args.limit, args.config) == (1, "tina.toml")


def test_dispatch_enqueues_up_to_the_limit(project: Path) -> None:
    source = FakeSource(items("VUL-1", "VUL-2", "VUL-3"))
    executor = FakeExecutor()

    code = cli.dispatch(config.load(project), "vul", 2, source=source, executor=executor)

    assert code == 0
    assert source.queried == "project = VUL"
    assert executor.enqueued == [("vul", "VUL-1"), ("vul", "VUL-2")]
    assert source.claimed == [], "the dispatcher never claims — workers do"


def test_dispatch_with_no_matches_enqueues_nothing(project: Path) -> None:
    executor = FakeExecutor()

    cli.dispatch(config.load(project), "vul", 5, source=FakeSource([]), executor=executor)

    assert executor.enqueued == []


def test_run_invokes_the_agent_and_records_the_outcome(project: Path, records: io.StringIO) -> None:
    outcome_written(
        project,
        {
            "outcome": "resolved",
            "details": "opened a PR",
            "artifacts": [{"kind": "github:pr", "url": GOOD_ARTIFACT}],
        },
    )
    source = FakeSource(items("VUL-1"))

    code = cli.run(config.load(project), "vul", "VUL-1", source=source)

    record = last_record(records.getvalue())
    assert code == 0
    assert source.claimed == ["VUL-1"]
    assert record["workflow"] == "vul"
    assert record["item"] == "VUL-1"
    assert record["report"]["outcome"] == "resolved"
    assert record["report"]["details"] == "opened a PR"
    assert record["report"]["verified"] is True
    assert record["effective_status"] == OutcomeStatus.RESOLVED
    assert record["exit_code"] == 0


def test_run_flips_the_status_when_an_artifact_is_missing(
    project: Path, records: io.StringIO
) -> None:
    outcome_written(
        project,
        {
            "outcome": "resolved",
            "details": "opened a PR",
            "artifacts": [{"kind": "github:pr", "url": MISSING_ARTIFACT}],
        },
    )

    code = cli.run(config.load(project), "vul", "VUL-1", source=FakeSource(items("VUL-1")))

    record = last_record(records.getvalue())
    assert code == 0, "an unverified artifact is a finding, not a tina failure"
    assert record["report"]["outcome"] == "resolved", "the agent's report is preserved"
    assert record["report"]["verified"] is False
    assert record["effective_status"] == OutcomeStatus.NEEDS_HUMAN


def test_run_without_artifacts_is_not_verified(project: Path, records: io.StringIO) -> None:
    outcome_written(project, {"outcome": "resolved", "details": "already fixed upstream"})

    cli.run(config.load(project), "vul", "VUL-1", source=FakeSource(items("VUL-1")))

    record = last_record(records.getvalue())
    assert record["report"]["verified"] is None
    assert record["effective_status"] == OutcomeStatus.RESOLVED


def test_a_failed_agent_is_still_a_successful_run(project: Path, records: io.StringIO) -> None:
    """The outcome is data, not a process failure."""
    outcome_written(project, {"outcome": "failed", "details": "no credentials for the repo"})

    code = cli.run(config.load(project), "vul", "VUL-1", source=FakeSource(items("VUL-1")))

    assert code == 0
    assert last_record(records.getvalue())["effective_status"] == OutcomeStatus.FAILED


def test_a_lost_claim_exits_no_action_needed(project: Path, records: io.StringIO) -> None:
    source = FakeSource(items("VUL-1"), claimable=False)

    code = cli.run(config.load(project), "vul", "VUL-1", source=source)

    record = last_record(records.getvalue())
    assert code == 0
    assert record["effective_status"] == OutcomeStatus.NO_ACTION_NEEDED
    assert record["exit_code"] is None, "the harness never ran"


def test_main_reports_a_bad_config(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["dispatch", "--workflow", "vul", "--config", "/nonexistent/tina.toml"])

    assert code == 1
    assert "not found" in last_record(capsys.readouterr().out)["message"]


def test_main_reports_an_unknown_workflow(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["run", "--workflow", "bug", "--item", "1", "--config", str(project)])

    assert code == 1
    assert "no workflow named 'bug'" in last_record(capsys.readouterr().out)["message"]
