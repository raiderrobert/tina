"""Cloud Run executor: one job execution per work item, against the same image.

Requires the optional dependency: `pip install tina-cli[cloudrun]`.
"""

from __future__ import annotations

import itertools
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tina.config import CloudRunOptions
from tina.executors.base import ExecutorError
from tina.log import get_logger
from tina.models import SWEEP_ITEM

log = get_logger(__name__)

#: Cloud Run injects the execution name into every job task's environment.
EXECUTION_ENV = "CLOUD_RUN_EXECUTION"

#: The Admin API sheds load with 503 UNAVAILABLE. One wait per retry, then the
#: error stands. Every other failure is permanent and raises immediately.
RUN_JOB_WAITS = (2.0, 8.0, 20.0)

#: How many executions `running` examines, newest first, before it stops
#: paging. Workers have finite timeouts, so anything deeper than this many
#: launches has long since finished; paging further would only spend quota.
RUNNING_HORIZON = 200

INSTALL_HINT = (
    "install tina with the extra: `uv add 'tina-cli[cloudrun]'` or "
    "`pip install 'tina-cli[cloudrun]'`"
)


class CloudRunExecutor:
    """Create a Cloud Run job execution with per-item args overrides."""

    name = "cloudrun"

    def __init__(
        self,
        options: CloudRunOptions,
        config_path: Path | str = "tina.toml",
        client: Any = None,
        sleep: Callable[[float], None] = time.sleep,
        executions_client: Any = None,
    ) -> None:
        # Required keys are enforced by CloudRunOptions at config load, so a
        # misconfigured job fails before the first query runs, not after.
        self.options = options
        self.config_path = str(config_path)
        self._client = client
        self._sleep = sleep
        self._executions_client = executions_client

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = _jobs_client()
        return self._client

    @property
    def executions_client(self) -> Any:
        if self._executions_client is None:
            self._executions_client = _run_v2().ExecutionsClient()
        return self._executions_client

    @property
    def job_path(self) -> str:
        return self.options.job_path()

    def enqueue(self, track: str, item_id: str | None = None) -> None:
        run_v2 = _run_v2()
        args = ["run", "--track", track]
        if item_id is not None:
            args += ["--item", item_id]
        args += ["--config", self.config_path]
        request = run_v2.RunJobRequest(
            name=self.job_path,
            overrides=run_v2.RunJobRequest.Overrides(
                container_overrides=[run_v2.RunJobRequest.Overrides.ContainerOverride(args=args)]
            ),
        )
        self._run_job(request)
        log.info(
            "worker enqueued",
            extra={"track": track, "item": item_id or "", "job": self.job_path},
        )

    def _run_job(self, request: Any) -> None:
        """Create the execution, retrying while the Admin API sheds load."""
        for wait in RUN_JOB_WAITS:
            try:
                self.client.run_job(request=request)
                return
            except Exception as exc:
                if not _is_unavailable(exc):
                    raise
                log.warning(
                    "cloud run unavailable; retrying",
                    extra={"job": self.job_path, "wait_seconds": wait},
                )
                self._sleep(wait)
        self.client.run_job(request=request)

    def running(self, track: str) -> list[str]:
        """Item ids read back from the args `enqueue` set on live executions.

        Live means no completion time yet. The listing comes back newest
        first and stops at `RUNNING_HORIZON` executions; nothing is stored
        between calls (ADR-016). A worker whose args name another track is
        someone else's business, and an item-less one is the sweep marker.
        """
        in_flight: list[str] = []
        executions = self.executions_client.list_executions(parent=self.job_path)
        for execution in itertools.islice(executions, RUNNING_HORIZON):
            if execution.completion_time:
                continue
            args = _execution_args(execution)
            if _flag_value(args, "--track") != track:
                continue
            in_flight.append(_flag_value(args, "--item") or SWEEP_ITEM)
        return in_flight

    def run_url(self) -> str | None:
        """Console URL of the execution this worker is running inside.

        The execution name comes from the environment; project and region
        from the options. Outside a job there is no execution, so None —
        never a URL that 404s. Needs no client and no SDK, which is what
        lets `tina run` build this executor just to ask one question.
        """
        execution = os.environ.get(EXECUTION_ENV)
        if not execution:
            return None
        return (
            "https://console.cloud.google.com/run/jobs/executions/details/"
            f"{self.options.region}/{execution}/logs?project={self.options.project}"
        )


def _execution_args(execution: Any) -> list[str]:
    """The worker argv the execution runs — where `enqueue` put the overrides."""
    containers = execution.template.containers
    return list(containers[0].args) if containers else []


def _flag_value(args: list[str], flag: str) -> str | None:
    """The value following `flag`, as `enqueue` lays argv out."""
    for name, value in itertools.pairwise(args):
        if name == flag:
            return value
    return None


def _is_unavailable(exc: Exception) -> bool:
    """503 UNAVAILABLE, matched on the google exception's own status attribute
    so the check needs nothing imported from the optional extra."""
    return getattr(exc, "code", None) == 503


def _run_v2() -> Any:
    try:
        # only present with the `cloudrun` extra
        from google.cloud import run_v2
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ExecutorError(
            "the cloudrun executor needs google-cloud-run", fix=INSTALL_HINT
        ) from exc
    return run_v2


def _jobs_client() -> Any:
    return _run_v2().JobsClient()
