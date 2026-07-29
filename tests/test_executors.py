from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tina import executors
from tina.config import Config
from tina.executors.base import ExecutorError
from tina.executors.cloudrun import CloudRunExecutor
from tina.executors.local import LocalExecutor


def test_local_executor_spawns_a_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    LocalExecutor(config_path=Path("/etc/tina.toml")).enqueue("vul", "VUL-1")

    assert calls == [
        [
            sys.executable,
            "-m",
            "tina",
            "run",
            "--workflow",
            "vul",
            "--item",
            "VUL-1",
            "--config",
            "/etc/tina.toml",
        ]
    ]


def test_local_executor_reports_a_failure_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(command: list[str], **kwargs: Any) -> None:
        raise OSError("too many processes")

    monkeypatch.setattr(subprocess, "run", boom)

    with pytest.raises(ExecutorError, match="could not start a worker"):
        LocalExecutor(config_path=Path("tina.toml")).enqueue("vul", "VUL-1")


def test_cloudrun_requires_its_config() -> None:
    with pytest.raises(ExecutorError, match="project, region, job"):
        CloudRunExecutor.from_config({}, "tina.toml")


def test_cloudrun_job_path() -> None:
    executor = CloudRunExecutor.from_config(
        {"project": "p", "region": "us-central1", "job": "tina-worker"}, "tina.toml"
    )

    assert executor.job_path == "projects/p/locations/us-central1/jobs/tina-worker"


def test_build_selects_by_name(tmp_path: Path) -> None:
    config = Config(path=tmp_path / "tina.toml", harness="pi")

    assert isinstance(executors.build(config), LocalExecutor)


def test_build_rejects_an_unknown_executor(tmp_path: Path) -> None:
    config = Config(path=tmp_path / "tina.toml", harness="pi", executor="nomad")

    with pytest.raises(ExecutorError, match="nomad"):
        executors.build(config)
