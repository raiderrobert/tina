from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tina import executors
from tina.config import CloudRunOptions, Config, ConfigError
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
            "--track",
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


def test_cloudrun_requires_its_options_table(tmp_path: Path) -> None:
    config = Config(path=tmp_path / "tina.toml", harness="pi", executor="cloudrun")

    with pytest.raises(ConfigError, match=r"\[executors.cloudrun\] table"):
        executors.build(config)


def test_cloudrun_job_path() -> None:
    executor = CloudRunExecutor(
        CloudRunOptions(project="p", region="us-central1", job="tina-worker")
    )

    assert executor.job_path == "projects/p/locations/us-central1/jobs/tina-worker"


def test_build_selects_by_name(tmp_path: Path) -> None:
    config = Config(path=tmp_path / "tina.toml", harness="pi")

    assert isinstance(executors.build(config), LocalExecutor)


def test_build_rejects_an_unknown_executor(tmp_path: Path) -> None:
    config = Config(path=tmp_path / "tina.toml", harness="pi", executor="nomad")

    with pytest.raises(ExecutorError, match="nomad"):
        executors.build(config)


# --- item-less enqueue: how a sweep worker starts -----------------------------


def test_local_executor_spawns_an_item_less_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """No --item pair at all — `tina run` decides what an absent item means."""
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    LocalExecutor(config_path=Path("/etc/tina.toml")).enqueue("reap")

    assert calls == [
        [
            sys.executable,
            "-m",
            "tina",
            "run",
            "--track",
            "reap",
            "--config",
            "/etc/tina.toml",
        ]
    ]


class FakeJobsClient:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    def run_job(self, request: Any) -> None:
        self.requests.append(request)


def test_cloudrun_executor_omits_the_item_from_the_args() -> None:
    client = FakeJobsClient()
    executor = CloudRunExecutor(
        CloudRunOptions(project="p", region="r", job="j"),
        config_path="/etc/tina.toml",
        client=client,
    )

    executor.enqueue("reap")

    (request,) = client.requests
    args = list(request.overrides.container_overrides[0].args)
    assert args == ["run", "--track", "reap", "--config", "/etc/tina.toml"]


def test_cloudrun_executor_passes_the_item_when_given() -> None:
    client = FakeJobsClient()
    executor = CloudRunExecutor(
        CloudRunOptions(project="p", region="r", job="j"),
        config_path="/etc/tina.toml",
        client=client,
    )

    executor.enqueue("vul", "VUL-1")

    (request,) = client.requests
    args = list(request.overrides.container_overrides[0].args)
    assert args == ["run", "--track", "vul", "--item", "VUL-1", "--config", "/etc/tina.toml"]


# --- run_url: a deep link to the worker's own logs ---------------------------


def test_local_run_url_is_none() -> None:
    """There is no log console; inventing a file path would be worse."""
    assert LocalExecutor(config_path=Path("tina.toml")).run_url() is None


def test_cloudrun_run_url_is_built_from_the_worker_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLOUD_RUN_EXECUTION", "tina-worker-abc12")
    executor = CloudRunExecutor(
        CloudRunOptions(project="acme-prod", region="us-central1", job="tina-worker")
    )

    assert executor.run_url() == (
        "https://console.cloud.google.com/run/jobs/executions/details/"
        "us-central1/tina-worker-abc12/logs?project=acme-prod"
    )


def test_cloudrun_run_url_is_none_outside_a_job() -> None:
    """No execution name, no URL — never a link that 404s. clean_env holds here."""
    executor = CloudRunExecutor(CloudRunOptions(project="p", region="r", job="j"))

    assert executor.run_url() is None
