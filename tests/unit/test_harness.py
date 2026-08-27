from __future__ import annotations

import json
import os
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


def test_track_env_is_merged_over_the_inherited_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The subprocess sees both; tina's own environment is never touched."""
    monkeypatch.setenv("INHERITED_VAR", "from-parent")
    fake = script(
        tmp_path,
        "import json, os, pathlib, sys\n"
        "pathlib.Path(sys.argv[2], 'outcome.json').write_text(json.dumps({\n"
        "    'outcome': 'resolved',\n"
        "    'details': os.environ['TRACK_VAR'] + '/' + os.environ['INHERITED_VAR'],\n"
        "}))\n",
    )

    result = harness.run(
        config(sys.executable, str(fake), "{prompt_file}", "{outcome_dir}"),
        "prompt",
        tmp_path / "run",
        env={"TRACK_VAR": "from-track"},
    )

    assert result.report.details == "from-track/from-parent"
    assert "TRACK_VAR" not in os.environ, "merged for the subprocess only"


def test_no_track_env_inherits_the_environment_untouched(tmp_path: Path) -> None:
    fake = script(
        tmp_path,
        "import json, os, pathlib, sys\n"
        "pathlib.Path(sys.argv[2], 'outcome.json').write_text(json.dumps({\n"
        "    'outcome': 'resolved', 'details': str('PATH' in os.environ),\n"
        "}))\n",
    )

    result = harness.run(
        config(sys.executable, str(fake), "{prompt_file}", "{outcome_dir}"),
        "prompt",
        tmp_path / "run",
    )

    assert result.report.details == "True"


def test_a_session_dir_is_created_and_substituted(tmp_path: Path) -> None:
    """The harness writes whatever it writes there; tina only provides the path."""
    fake = script(
        tmp_path,
        "import pathlib, sys\n"
        "pathlib.Path(sys.argv[3], 'transcript.jsonl').write_text('turn one\\n')\n"
        "pathlib.Path(sys.argv[2], 'outcome.json').write_text('{\"outcome\": \"resolved\"}')\n",
    )
    workdir = tmp_path / "run"

    result = harness.run(
        config(sys.executable, str(fake), "{prompt_file}", "{outcome_dir}", "{session_dir}"),
        "prompt",
        workdir,
    )

    assert result.session_dir is not None
    assert result.session_dir.is_dir(), "fresh and ready before the harness starts"
    assert (result.session_dir / "transcript.jsonl").read_text() == "turn one\n"


def test_no_session_dir_reference_creates_no_directory(tmp_path: Path) -> None:
    fake = script(
        tmp_path,
        "import pathlib, sys\n"
        "pathlib.Path(sys.argv[2], 'outcome.json').write_text('{\"outcome\": \"resolved\"}')\n",
    )
    workdir = tmp_path / "run"

    result = harness.run(
        config(sys.executable, str(fake), "{prompt_file}", "{outcome_dir}"), "prompt", workdir
    )

    assert result.session_dir is None
    assert not (workdir / harness.SESSION_DIR).exists()


def test_capture_copies_the_session_contents_under_the_item(tmp_path: Path) -> None:
    session = tmp_path / "session"
    (session / "sub").mkdir(parents=True)
    (session / "transcript.jsonl").write_text("turn one\n")
    (session / "sub" / "cost.json").write_text("{}")
    artifacts = tmp_path / "artifacts"

    harness.capture(session, artifacts, "VUL-1")

    assert (artifacts / "VUL-1" / "transcript.jsonl").read_text() == "turn one\n"
    assert (artifacts / "VUL-1" / "sub" / "cost.json").read_text() == "{}"


def test_capture_without_an_artifacts_dir_copies_nothing_and_warns_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    session = tmp_path / "session"
    session.mkdir()
    (session / "transcript.jsonl").write_text("turn one\n")

    harness.capture(session, None, "VUL-1")

    assert caplog.records == []


def test_capture_without_a_session_dir_copies_nothing_and_warns_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    harness.capture(None, tmp_path / "artifacts", "VUL-1")

    assert caplog.records == []
    assert not (tmp_path / "artifacts").exists()


def test_a_capture_failure_is_logged_and_never_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Best-effort: the evidence is lost, the run is not."""
    session = tmp_path / "session"
    session.mkdir()

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("bucket mount went away")

    monkeypatch.setattr(harness.shutil, "copytree", boom)

    harness.capture(session, tmp_path / "artifacts", "VUL-1")

    assert any("artifact capture failed" in record.message for record in caplog.records)


def test_a_bogus_artifact_url_is_a_broken_report(tmp_path: Path) -> None:
    """`"url": "TBD"` is not an artifact that 404s — it is an invalid report."""
    path = tmp_path / "outcome.json"
    path.write_text(
        json.dumps({"outcome": "resolved", "artifacts": [{"kind": "pr", "url": "TBD"}]})
    )

    report = harness.read_outcome(path, exit_code=0)

    assert report.outcome is OutcomeStatus.FAILED
    assert "invalid outcome.json" in report.details
