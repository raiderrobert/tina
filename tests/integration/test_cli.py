from __future__ import annotations

import importlib
import importlib.metadata
import io
import json
import logging
import re
import shlex
import sys
from collections.abc import Iterator
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

import tina
from tina import cli, config, executors, harness, log, sources, verify
from tina.models import OutcomeReport, OutcomeStatus, WorkItem
from tina.sources.base import ClaimPrognosis

# The one URL the stubbed verifier considers real.
GOOD_ARTIFACT = "https://example.test/pr/1"
MISSING_ARTIFACT = "https://example.test/pr/999"

runner = CliRunner()

CONFIG = """
harness = "fake"
tracks_dir = "tracks"

[harnesses.fake]
command = ["{python}", "{script}", "{{prompt_file}}", "{{outcome_dir}}"]

[vul]
source = "jira"
query = "project = VUL"
track = "remediate"
result = "github:pr"
"""

# A fake agent: reads the prompt, writes the outcome it was told to write.
AGENT = """\
import pathlib, sys
prompt = pathlib.Path(sys.argv[1]).read_text()
outcome = pathlib.Path(__file__).with_name("outcome_to_write.json").read_text()
assert "Remediate the vulnerability" in prompt, "track skill missing from prompt"
assert "VUL-1" in prompt, "work item missing from prompt"
pathlib.Path(sys.argv[2], "outcome.json").write_text(outcome)
"""


class FakeSource:
    """Stands in for a tracker. Records what the CLI asked it to do."""

    def __init__(self, items: list[WorkItem], claimable: bool = True, holder: str = "") -> None:
        self.items = items
        self.claimable = claimable
        self.holder = holder
        self.claimed: list[str] = []

    def query(self, q: str) -> list[WorkItem]:
        self.queried = q
        return list(self.items)

    def get(self, item_id: str) -> WorkItem:
        return next(item for item in self.items if item.id == item_id)

    def claim(self, item: WorkItem) -> bool:
        self.claimed.append(item.id)
        return self.claimable

    def claim_prognosis(self, item: WorkItem) -> ClaimPrognosis:
        return ClaimPrognosis(would_claim=self.claimable, holder=self.holder)


class NoClaimSource(FakeSource):
    """A tracker that fails the test if the claim it must not make is made.

    The guarantee is the absence of the call, not a branch someone read.
    """

    def claim(self, item: WorkItem) -> bool:
        raise AssertionError("a dry run must never claim")


class FakeExecutor:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, str]] = []

    def enqueue(self, track: str, item_id: str) -> None:
        self.enqueued.append((track, item_id))


@pytest.fixture
def records() -> Iterator[io.StringIO]:
    """The JSON log lines the entrypoint functions emit, collected in memory.

    The typer commands call `log.configure()` themselves; `dispatch_track`
    and `run_item` do not, so tests that skip the command layer install the same
    formatter here.
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
    """A config, a track skill, and a fake harness on disk."""
    script = tmp_path / "agent.py"
    script.write_text(AGENT)
    (script.parent / "outcome_to_write.json").write_text(
        json.dumps({"outcome": "no_action_needed", "details": "nothing to do"})
    )

    track = tmp_path / "tracks" / "remediate"
    track.mkdir(parents=True)
    (track / "SKILL.md").write_text("# Remediate the vulnerability\n")

    path = tmp_path / "tina.toml"
    path.write_text(CONFIG.format(python=sys.executable, script=script))
    return path


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeSource, FakeExecutor]:
    """Point the adapter factories at fakes, so the real argv path can be driven."""
    source = FakeSource(items("VUL-1", "VUL-2", "VUL-3"))
    executor = FakeExecutor()
    monkeypatch.setattr(sources, "build", lambda track, client=None: source)
    monkeypatch.setattr(executors, "build", lambda config: executor)
    return source, executor


def wire(monkeypatch: pytest.MonkeyPatch, source: FakeSource) -> FakeSource:
    """Point `sources.build` at one specific fake, and hand it back."""
    monkeypatch.setattr(sources, "build", lambda track, client=None: source)
    return source


def outcome_written(project: Path, payload: dict[str, Any]) -> None:
    (project.parent / "outcome_to_write.json").write_text(json.dumps(payload))


def last_record(output: str) -> dict[str, Any]:
    """The final JSON line: the RunRecord, or the error that stopped the run."""
    lines = [line for line in output.splitlines() if line.startswith("{")]
    assert lines, "expected at least one JSON log line"
    return json.loads(lines[-1])


def json_lines(output: str) -> list[dict[str, Any]]:
    """Every JSON log line, in order. stdout is one object per line."""
    return [json.loads(line) for line in output.splitlines() if line.startswith("{")]


def items(*ids: str) -> list[WorkItem]:
    return [WorkItem(id=item_id, source="jira", title=item_id) for item_id in ids]


# --- the command layer: argv, defaults, exit codes -------------------------


def plain(output: str) -> str:
    """CLI output with rich's ANSI styling stripped.

    CI sets `GITHUB_ACTIONS`, which rich reads as "this is a terminal", so help
    text comes back styled there but not locally. Rich emits an option name as
    two adjacent spans — `--version` renders as
    `\\x1b[1;36m-\\x1b[0m\\x1b[1;36m-version\\x1b[0m` — so the flag is not a
    substring of the raw output. Strip the escapes before asserting on it.
    """
    return re.sub(r"\x1b\[[0-9;]*m", "", output)


def test_help_lists_both_roles() -> None:
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "dispatch" in plain(result.output)
    assert "run" in plain(result.output)


def test_help_lists_the_version_flag() -> None:
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "--version" in plain(result.output)


def test_no_arguments_still_prints_help() -> None:
    """`no_args_is_help=True` survives the new callback."""
    result = runner.invoke(cli.app, [])

    assert "dispatch" in plain(result.output)
    assert "run" in plain(result.output)


def test_version_prints_the_package_version() -> None:
    result = runner.invoke(cli.app, ["--version"])

    assert result.exit_code == 0
    assert result.output == f"tina {tina.__version__}\n"


def test_version_needs_no_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The flag is eager: no tina.toml, no config load, still exit 0."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli.app, ["--version"])

    assert result.exit_code == 0
    assert result.output == f"tina {tina.__version__}\n"


def test_version_falls_back_when_the_distribution_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reading `tina.__version__` at call time keeps the fallback visible."""

    def not_found(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", not_found)
    try:
        importlib.reload(tina)
        result = runner.invoke(cli.app, ["--version"])

        assert result.exit_code == 0
        assert result.output == f"tina {tina.FALLBACK_VERSION}\n"
    finally:
        monkeypatch.undo()
        importlib.reload(tina)


def test_dispatch_defaults_to_one_worker_and_tina_toml(
    project: Path, wired: tuple[FakeSource, FakeExecutor]
) -> None:
    _, executor = wired

    result = runner.invoke(cli.app, ["dispatch", "--track", "vul", "--config", str(project)])

    assert result.exit_code == 0
    assert executor.enqueued == [("vul", "VUL-1")], "--limit defaults to 1"


def test_config_defaults_to_tina_toml_in_the_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli.app, ["dispatch", "--track", "vul"])

    assert result.exit_code == 1
    assert "tina.toml" in last_record(result.output)["message"]


def test_limit_is_passed_through(project: Path, wired: tuple[FakeSource, FakeExecutor]) -> None:
    _, executor = wired

    result = runner.invoke(
        cli.app, ["dispatch", "--track", "vul", "--limit", "2", "--config", str(project)]
    )

    assert result.exit_code == 0
    assert executor.enqueued == [("vul", "VUL-1"), ("vul", "VUL-2")]


def test_run_exits_zero_when_the_agent_reports_failed(
    project: Path, wired: tuple[FakeSource, FakeExecutor]
) -> None:
    """The outcome is data, not a process failure."""
    outcome_written(project, {"outcome": "failed", "details": "no credentials for the repo"})

    result = runner.invoke(
        cli.app, ["run", "--track", "vul", "--item", "VUL-1", "--config", str(project)]
    )

    assert result.exit_code == 0
    assert last_record(result.output)["effective_status"] == OutcomeStatus.FAILED


def test_missing_required_option_is_a_usage_error(project: Path) -> None:
    result = runner.invoke(cli.app, ["run", "--track", "vul", "--config", str(project)])

    assert result.exit_code == 2, "typer reports a usage error, not a tina error"


def test_reports_a_bad_config() -> None:
    result = runner.invoke(
        cli.app, ["dispatch", "--track", "vul", "--config", "/nonexistent/tina.toml"]
    )

    assert result.exit_code == 1
    assert "not found" in last_record(result.output)["message"]


def test_reports_an_unknown_track(project: Path) -> None:
    result = runner.invoke(
        cli.app, ["run", "--track", "bug", "--item", "1", "--config", str(project)]
    )

    assert result.exit_code == 1
    assert "no track named 'bug'" in last_record(result.output)["message"]


# --- both halves of the stdout/stderr boundary ------------------------------


def test_a_toml_typo_reports_on_both_streams(tmp_path: Path) -> None:
    """The JSON error line still parses off stdout; the fix block lands on stderr."""
    path = tmp_path / "tina.toml"
    path.write_text('harness = "pi"\n\n[harness.pi]\ncommand = ["pi"]\n')

    result = runner.invoke(cli.app, ["dispatch", "--track", "vul", "--config", str(path)])

    assert result.exit_code == 1
    assert "invalid TOML" in last_record(result.stdout)["message"]
    stderr = plain(result.stderr)
    assert "✗ " in stderr
    assert "Fix:   harness/executor select an adapter by name" in stderr
    assert "[harnesses.<name>]" in stderr


def test_a_missing_env_var_reports_on_both_streams(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `wired` fixture here, so the real source is built and require_env fires."""
    for name in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    result = runner.invoke(cli.app, ["dispatch", "--track", "vul", "--config", str(project)])

    assert result.exit_code == 1
    assert "JIRA_BASE_URL" in last_record(result.stdout)["message"]
    assert "Fix:   Set JIRA_BASE_URL in the worker environment." in plain(result.stderr)


# --- the orchestration layer -----------------------------------------------


def test_dispatch_enqueues_up_to_the_limit(project: Path) -> None:
    source = FakeSource(items("VUL-1", "VUL-2", "VUL-3"))
    executor = FakeExecutor()

    cli.dispatch_track(config.load(project), "vul", 2, source=source, executor=executor)

    assert source.queried == "project = VUL"
    assert executor.enqueued == [("vul", "VUL-1"), ("vul", "VUL-2")]
    assert source.claimed == [], "the dispatcher never claims — workers do"


def test_dispatch_with_no_matches_enqueues_nothing(project: Path) -> None:
    executor = FakeExecutor()

    cli.dispatch_track(config.load(project), "vul", 5, source=FakeSource([]), executor=executor)

    assert executor.enqueued == []


# --- dry run: everything a dispatch does except the last step ---------------


def test_a_dry_run_enqueues_nothing(project: Path, wired: tuple[FakeSource, FakeExecutor]) -> None:
    """The query ran; the enqueue did not. Driven through the real argv path."""
    source, executor = wired

    result = runner.invoke(
        cli.app, ["dispatch", "--track", "vul", "--config", str(project), "--dry-run"]
    )

    assert result.exit_code == 0
    assert source.queried == "project = VUL"
    assert executor.enqueued == []
    assert source.claimed == [], "the dispatcher never claims — workers do"


def test_a_dry_run_builds_no_executor(
    project: Path, wired: tuple[FakeSource, FakeExecutor], monkeypatch: pytest.MonkeyPatch
) -> None:
    """No LocalExecutor subprocess, no Cloud Run client — nothing constructed at all.

    Asserted with a spy rather than by reading the code: `build()` is where every
    executor side effect begins, so a build that never happens is the guarantee.
    """

    def fail(cfg: config.Config) -> executors.Executor:
        raise AssertionError("executors.build must not be called for a dry run")

    monkeypatch.setattr(executors, "build", fail)

    result = runner.invoke(
        cli.app, ["dispatch", "--track", "vul", "--config", str(project), "--dry-run"]
    )

    assert result.exception is None
    assert result.exit_code == 0


def test_a_dry_run_respects_the_limit(
    project: Path, wired: tuple[FakeSource, FakeExecutor]
) -> None:
    """--limit 2 against three items previews two."""
    result = runner.invoke(
        cli.app,
        ["dispatch", "--track", "vul", "--limit", "2", "--config", str(project), "--dry-run"],
    )

    previewed = [
        line["item"] for line in json_lines(result.stdout) if line["message"] == "would enqueue"
    ]
    assert result.exit_code == 0
    assert previewed == ["VUL-1", "VUL-2"]


def test_a_dry_run_with_no_matches_exits_zero(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero matches is a valid preview, not an error."""
    source, executor = FakeSource([]), FakeExecutor()
    monkeypatch.setattr(sources, "build", lambda track, client=None: source)
    monkeypatch.setattr(executors, "build", lambda config: executor)

    result = runner.invoke(
        cli.app,
        ["dispatch", "--track", "vul", "--limit", "5", "--config", str(project), "--dry-run"],
    )

    lines = json_lines(result.stdout)
    assert result.exit_code == 0
    assert executor.enqueued == []
    assert [line["message"] for line in lines] == ["dispatching"]
    assert lines[0]["matched"] == 0
    assert lines[0]["dry_run"] is True
    assert "0 items matched (limit 5)." in plain(result.stderr)


def test_the_would_enqueue_line_keeps_the_enqueued_schema(
    project: Path, wired: tuple[FakeSource, FakeExecutor]
) -> None:
    """Same fields as `enqueued`, plus the marker; a different message on purpose."""
    argv = ["dispatch", "--track", "vul", "--config", str(project)]
    real = json_lines(runner.invoke(cli.app, argv).stdout)
    preview = json_lines(runner.invoke(cli.app, [*argv, "--dry-run"]).stdout)

    enqueued = next(line for line in real if line["message"] == "enqueued")
    would = next(line for line in preview if line["message"] == "would enqueue")

    assert set(would) - set(enqueued) == {"dry_run"}
    assert set(enqueued) - set(would) == set()
    assert set(enqueued) == {
        "ts",
        "level",
        "logger",
        "message",
        "track",
        "item",
        "url",
        "executor",
    }
    assert would["dry_run"] is True
    assert (would["item"], would["url"], would["executor"]) == (
        enqueued["item"],
        enqueued["url"],
        enqueued["executor"],
    )


def test_a_normal_dispatch_emits_no_dry_run_key(
    project: Path, wired: tuple[FakeSource, FakeExecutor]
) -> None:
    """The marker is present only when the flag is."""
    result = runner.invoke(cli.app, ["dispatch", "--track", "vul", "--config", str(project)])

    assert result.exit_code == 0
    assert all("dry_run" not in line for line in json_lines(result.stdout))
    assert result.stderr == "", "a normal dispatch prints nothing new on stderr"


def test_the_dry_run_preview_lands_on_stderr(
    project: Path, wired: tuple[FakeSource, FakeExecutor]
) -> None:
    """Header, one line per match in order, tally, footer — and none of it on stdout."""
    result = runner.invoke(
        cli.app,
        ["dispatch", "--track", "vul", "--limit", "2", "--config", str(project), "--dry-run"],
    )

    assert result.exit_code == 0
    assert plain(result.stderr).splitlines() == [
        "Dry run — no workers will be enqueued",
        "",
        "  Would enqueue VUL-1 via local — VUL-1",
        "  Would enqueue VUL-2 via local — VUL-2",
        "",
        "2 items matched (limit 2).",
        "",
        "Run without --dry-run to enqueue.",
    ]
    assert "Would enqueue" not in result.stdout, "the preview is prose; stdout stays JSON"


def test_a_dry_run_drops_an_empty_title(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """No title, no dangling dash."""
    source = FakeSource([WorkItem(id="VUL-9", source="jira")])

    cli.dispatch_track(config.load(project), "vul", 1, source=source, dry_run=True)

    err = plain(capsys.readouterr().err)
    assert "  Would enqueue VUL-9 via local\n" in err
    assert "VUL-9 via local —" not in err


def test_dispatch_help_lists_the_dry_run_flag() -> None:
    result = runner.invoke(cli.app, ["dispatch", "--help"])

    assert result.exit_code == 0
    assert "--dry-run" in plain(result.output)


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

    cli.run_item(config.load(project), "vul", "VUL-1", source=source)

    record = last_record(records.getvalue())
    assert source.claimed == ["VUL-1"]
    assert record["track"] == "vul"
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

    record = cli.run_item(config.load(project), "vul", "VUL-1", source=FakeSource(items("VUL-1")))

    assert record is not None, "a real run always returns its record"
    assert record.report.outcome is OutcomeStatus.RESOLVED, "the agent's report is preserved"
    assert record.report.verified is False
    assert record.effective_status is OutcomeStatus.NEEDS_HUMAN
    assert last_record(records.getvalue())["effective_status"] == OutcomeStatus.NEEDS_HUMAN


def test_run_without_artifacts_is_not_verified(project: Path, records: io.StringIO) -> None:
    outcome_written(project, {"outcome": "resolved", "details": "already fixed upstream"})

    cli.run_item(config.load(project), "vul", "VUL-1", source=FakeSource(items("VUL-1")))

    record = last_record(records.getvalue())
    assert record["report"]["verified"] is None
    assert record["effective_status"] == OutcomeStatus.RESOLVED


def test_a_lost_claim_exits_no_action_needed(project: Path, records: io.StringIO) -> None:
    source = FakeSource(items("VUL-1"), claimable=False)

    record = cli.run_item(config.load(project), "vul", "VUL-1", source=source)

    assert record is not None, "a real run always returns its record"
    assert record.effective_status is OutcomeStatus.NO_ACTION_NEEDED
    assert record.exit_code is None, "the harness never ran"
    assert last_record(records.getvalue())["effective_status"] == OutcomeStatus.NO_ACTION_NEEDED


# --- dry run: everything a run does except the parts that change something ---

DRY_RUN_ARGV = ["run", "--track", "vul", "--item", "VUL-1", "--dry-run"]


def test_a_dry_run_never_claims(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The item was fetched; the claim was not attempted."""
    wire(monkeypatch, NoClaimSource(items("VUL-1")))

    result = runner.invoke(cli.app, [*DRY_RUN_ARGV, "--config", str(project)])

    assert result.exception is None
    assert result.exit_code == 0


def test_a_dry_run_runs_no_harness(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No subprocess, ever. `harness.run` is where every one of them starts."""
    wire(monkeypatch, FakeSource(items("VUL-1")))

    def fail(*args: Any, **kwargs: Any) -> harness.HarnessResult:
        raise AssertionError("harness.run must not be called for a dry run")

    monkeypatch.setattr(harness, "run", fail)

    result = runner.invoke(cli.app, [*DRY_RUN_ARGV, "--config", str(project)])

    assert result.exception is None
    assert result.exit_code == 0


def test_a_dry_run_logs_one_would_run_line(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The machine surface: `would run`, never `run complete`, and no run to report."""
    wire(monkeypatch, FakeSource(items("VUL-1")))

    result = runner.invoke(cli.app, [*DRY_RUN_ARGV, "--config", str(project)])
    lines = json_lines(result.stdout)

    assert result.exit_code == 0
    assert [line["message"] for line in lines] == ["would run"]
    assert set(lines[0]) == {
        "ts",
        "level",
        "logger",
        "message",
        "dry_run",
        "track",
        "item",
        "would_claim",
        "holder",
        "harness",
        "command",
        "prompt_file",
        "prompt_chars",
        "duration_seconds",
    }
    assert (lines[0]["dry_run"], lines[0]["would_claim"], lines[0]["holder"]) == (True, True, "")
    assert (lines[0]["track"], lines[0]["item"]) == ("vul", "VUL-1")
    assert lines[0]["harness"] == "fake"


def test_a_dry_run_of_a_held_item_stops_at_the_claim(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No prompt, no command — the real run would not get that far either."""
    wire(monkeypatch, FakeSource(items("VUL-1"), claimable=False, holder="alice"))

    result = runner.invoke(cli.app, [*DRY_RUN_ARGV, "--config", str(project)])
    line = last_record(result.stdout)

    assert result.exit_code == 0, "a preview of an item somebody holds is not an error"
    assert line["would_claim"] is False
    assert line["holder"] == "alice"
    assert line["effective_status"] == OutcomeStatus.NO_ACTION_NEEDED
    assert set(line) & {"harness", "command", "prompt_file", "prompt_chars"} == set()
    assert plain(result.stderr).splitlines() == [
        "Dry run — nothing will be claimed and no agent will run",
        "",
        "  Would not claim VUL-1 — held by alice; the run would exit no_action_needed",
        "",
        "Run without --dry-run to claim VUL-1 and run the agent.",
    ]


def test_a_dry_run_assembles_the_real_prompt(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read what was written, not merely that a path was printed."""
    wire(monkeypatch, FakeSource(items("VUL-1")))

    result = runner.invoke(cli.app, [*DRY_RUN_ARGV, "--config", str(project)])
    line = last_record(result.stdout)
    prompt_file = Path(line["prompt_file"])
    text = prompt_file.read_text(encoding="utf-8")

    assert prompt_file.name == "prompt.md"
    assert "Remediate the vulnerability" in text, "the track skill is in the prompt"
    assert "VUL-1" in text, "the work item is in the prompt"
    assert str(prompt_file.parent / "outcome.json") in text, "and where to write the outcome"
    assert line["prompt_chars"] == len(text)


def test_the_dry_run_command_is_the_one_a_real_run_would_invoke(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rendered through the same ArgvTemplate call `harness.run` makes."""
    wire(monkeypatch, FakeSource(items("VUL-1")))

    result = runner.invoke(cli.app, [*DRY_RUN_ARGV, "--config", str(project)])
    line = last_record(result.stdout)
    prompt_file = Path(line["prompt_file"])

    expected = config.load(project).harness_config().command.render(prompt_file, prompt_file.parent)
    assert line["command"] == expected


def test_the_run_preview_lands_on_stderr(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Header, the four steps in order, footer — and none of it on stdout."""
    wire(monkeypatch, FakeSource(items("VUL-1")))

    result = runner.invoke(cli.app, [*DRY_RUN_ARGV, "--config", str(project)])
    line = last_record(result.stdout)

    assert result.exit_code == 0
    assert plain(result.stderr).splitlines() == [
        "Dry run — nothing will be claimed and no agent will run",
        "",
        "  Would claim VUL-1 — unassigned",
        f"  Prompt assembled: {line['prompt_file']} ({line['prompt_chars']} chars)",
        f"  Would run: {shlex.join(line['command'])}",
        "  Would verify artifacts and record the outcome from the agent's outcome.json",
        "",
        "Run without --dry-run to claim VUL-1 and run the agent.",
    ]
    assert "Would claim" not in result.stdout, "the preview is prose; stdout stays JSON"


def test_a_normal_run_is_unaffected(project: Path, wired: tuple[FakeSource, FakeExecutor]) -> None:
    """The flag adds a mode; it does not change the one that was already there."""
    source, _ = wired

    result = runner.invoke(
        cli.app, ["run", "--track", "vul", "--item", "VUL-1", "--config", str(project)]
    )

    assert result.exit_code == 0
    assert source.claimed == ["VUL-1"]
    assert last_record(result.stdout)["message"] == "run complete"
    assert all("dry_run" not in line for line in json_lines(result.stdout))
    assert result.stderr == "", "a normal run prints nothing new on stderr"


def test_run_help_lists_the_dry_run_flag() -> None:
    result = runner.invoke(cli.app, ["run", "--help"])

    assert result.exit_code == 0
    assert "--dry-run" in plain(result.output)
