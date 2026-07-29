from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tina import harness
from tina.config import ArgvTemplate, HarnessConfig
from tina.models import OutcomeStatus


def script(tmp_path: Path, body: str) -> Path:
    """A fake harness: a Python one-liner run through the configured argv."""
    path = tmp_path / "fake_harness.py"
    path.write_text(body)
    return path


def config(*args: str) -> HarnessConfig:
    return HarnessConfig(name="fake", command=ArgvTemplate(args=list(args)))


def test_run_reads_the_outcome_the_agent_wrote(tmp_path: Path) -> None:
    fake = script(
        tmp_path,
        "import pathlib, sys\n"
        "prompt = pathlib.Path(sys.argv[1]).read_text()\n"
        "assert 'do the thing' in prompt\n"
        "pathlib.Path(sys.argv[2], 'outcome.json').write_text(\n"
        '    \'{"outcome": "resolved", "details": "opened a PR",'
        ' "artifacts": [{"kind": "github:pr", "url": "https://example.test/pr/1"}]}\'\n'
        ")\n",
    )
    workdir = tmp_path / "run"

    result = harness.run(
        config(sys.executable, str(fake), "{prompt_file}", "{outcome_dir}"),
        "do the thing",
        workdir,
    )

    assert result.exit_code == 0
    assert result.report.outcome is OutcomeStatus.RESOLVED
    assert str(result.report.artifacts[0].url) == "https://example.test/pr/1"
    assert (workdir / "prompt.md").read_text() == "do the thing"


def test_harness_stdout_is_never_parsed(tmp_path: Path) -> None:
    """A harness that prints a perfectly good outcome but writes no file has failed."""
    fake = script(tmp_path, 'print(\'{"outcome": "resolved"}\')\n')

    result = harness.run(
        config(sys.executable, str(fake), "{prompt_file}", "{outcome_dir}"),
        "prompt",
        tmp_path / "run",
    )

    assert result.report.outcome is OutcomeStatus.FAILED


def test_missing_binary_is_reported_not_raised(tmp_path: Path) -> None:
    result = harness.run(config("tina-no-such-binary", "{prompt_file}"), "prompt", tmp_path / "run")

    assert result.report.outcome is OutcomeStatus.FAILED
    assert "could not start harness" in result.report.details


def test_timeout_is_reported(tmp_path: Path) -> None:
    fake = script(tmp_path, "import time\ntime.sleep(30)\n")

    result = harness.run(
        config(sys.executable, str(fake), "{prompt_file}", "{outcome_dir}"),
        "prompt",
        tmp_path / "run",
        timeout=0.3,
    )

    assert result.report.outcome is OutcomeStatus.FAILED
    assert "timed out" in result.report.details
    assert result.exit_code is None


def test_read_outcome_missing_file_after_clean_exit(tmp_path: Path) -> None:
    report = harness.read_outcome(tmp_path / "outcome.json", exit_code=0)

    assert report.outcome is OutcomeStatus.FAILED
    assert report.details == "agent exited without writing outcome.json"


def test_read_outcome_missing_file_after_crash(tmp_path: Path) -> None:
    report = harness.read_outcome(tmp_path / "outcome.json", exit_code=137)

    assert report.outcome is OutcomeStatus.FAILED
    assert "137" in report.details


@pytest.mark.parametrize(
    "content",
    ["not json at all", json.dumps({"outcome": "half-done"}), json.dumps(["a", "list"])],
)
def test_read_outcome_rejects_unusable_files(tmp_path: Path, content: str) -> None:
    path = tmp_path / "outcome.json"
    path.write_text(content)

    report = harness.read_outcome(path, exit_code=0)

    assert report.outcome is OutcomeStatus.FAILED
    assert "invalid outcome.json" in report.details


def test_default_timeout_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TINA_HARNESS_TIMEOUT", "12")
    assert harness.default_timeout() == 12.0

    monkeypatch.setenv("TINA_HARNESS_TIMEOUT", "soon")
    assert harness.default_timeout() == harness.DEFAULT_TIMEOUT


def test_a_bogus_artifact_url_is_a_broken_report(tmp_path: Path) -> None:
    """`"url": "TBD"` is not an artifact that 404s — it is an invalid report."""
    path = tmp_path / "outcome.json"
    path.write_text(
        json.dumps({"outcome": "resolved", "artifacts": [{"kind": "pr", "url": "TBD"}]})
    )

    report = harness.read_outcome(path, exit_code=0)

    assert report.outcome is OutcomeStatus.FAILED
    assert "invalid outcome.json" in report.details
