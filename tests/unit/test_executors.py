from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tina import executors
from tina.config import CloudRunOptions, Config, ConfigError
from tina.executors.base import ExecutorError
from tina.executors.cloudrun import RUNNING_HORIZON, CloudRunExecutor
from tina.executors.local import LocalExecutor
from tina.models import SWEEP_ITEM


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


# --- run_job retry: the Admin API sheds load with 503 UNAVAILABLE -------------


class Unavailable(Exception):
    """The shape of google.api_core.exceptions.ServiceUnavailable that matters."""

    code = 503


class SheddingJobsClient(FakeJobsClient):
    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures

    def run_job(self, request: Any) -> None:
        super().run_job(request)
        if len(self.requests) <= self.failures:
            raise Unavailable("503 UNAVAILABLE")


def shedding_executor(failures: int) -> tuple[CloudRunExecutor, list[float]]:
    waits: list[float] = []
    executor = CloudRunExecutor(
        CloudRunOptions(project="p", region="r", job="j"),
        client=SheddingJobsClient(failures),
        sleep=waits.append,
    )
    return executor, waits


def test_run_job_is_retried_while_the_api_sheds_load() -> None:
    executor, waits = shedding_executor(failures=2)

    executor.enqueue("vul", "VUL-1")

    assert waits == [2.0, 8.0]
    assert len(executor.client.requests) == 3


def test_a_persistently_unavailable_api_raises_after_the_ladder() -> None:
    executor, waits = shedding_executor(failures=10)

    with pytest.raises(Unavailable):
        executor.enqueue("vul", "VUL-1")

    assert waits == [2.0, 8.0, 20.0]
    assert len(executor.client.requests) == 4


def test_other_run_job_errors_are_never_retried() -> None:
    class Denied(Exception):
        code = 403

    class DenyingJobsClient(FakeJobsClient):
        def run_job(self, request: Any) -> None:
            super().run_job(request)
            raise Denied("permission denied")

    waits: list[float] = []
    executor = CloudRunExecutor(
        CloudRunOptions(project="p", region="r", job="j"),
        client=DenyingJobsClient(),
        sleep=waits.append,
    )

    with pytest.raises(Denied):
        executor.enqueue("vul", "VUL-1")

    assert waits == []


# --- running: the workers still in flight, read back from the executor --------


def test_local_running_is_empty_honestly() -> None:
    """Workers are synchronous subprocesses; none can be in flight during dispatch."""
    assert LocalExecutor(config_path=Path("tina.toml")).running("vul") == []


class FakeExecution:
    """An execution as `running` reads it: args on the template, a completion time."""

    def __init__(self, args: list[str], done: bool = False) -> None:
        self.completion_time = "2026-08-27T12:00:00Z" if done else None
        self.template = SimpleNamespace(containers=[SimpleNamespace(args=args)])


class FakeExecutionsClient:
    def __init__(self, executions: list[FakeExecution]) -> None:
        self.executions = executions
        self.parents: list[str] = []

    def list_executions(self, parent: str) -> Iterator[FakeExecution]:
        self.parents.append(parent)
        return iter(self.executions)


def worker_args(track: str, item: str | None) -> list[str]:
    args = ["run", "--track", track]
    if item is not None:
        args += ["--item", item]
    return [*args, "--config", "/etc/tina.toml"]


def running_executor(executions: list[FakeExecution]) -> CloudRunExecutor:
    return CloudRunExecutor(
        CloudRunOptions(project="p", region="r", job="j"),
        executions_client=FakeExecutionsClient(executions),
    )


def test_cloudrun_running_reads_item_ids_from_live_executions() -> None:
    executor = running_executor(
        [
            FakeExecution(worker_args("vul", "VUL-1")),
            FakeExecution(worker_args("vul", "VUL-2"), done=True),
            FakeExecution(worker_args("bug", "77")),
            FakeExecution(worker_args("vul", "VUL-3")),
        ]
    )

    assert executor.running("vul") == ["VUL-1", "VUL-3"]
    assert executor.executions_client.parents == ["projects/p/locations/r/jobs/j"]


def test_cloudrun_running_names_an_item_less_worker_with_the_sweep_marker() -> None:
    executor = running_executor([FakeExecution(worker_args("reap", None))])

    assert executor.running("reap") == [SWEEP_ITEM]


def test_cloudrun_running_stops_paging_at_the_horizon() -> None:
    """A live worker deeper than the horizon is not seen — finite timeouts make
    the deep tail all terminal in practice, so paging further only spends quota."""
    finished = [
        FakeExecution(worker_args("vul", "VUL-old"), done=True) for _ in range(RUNNING_HORIZON)
    ]
    live = FakeExecution(worker_args("vul", "VUL-deep"))
    executor = running_executor([*finished, live])

    assert executor.running("vul") == []
