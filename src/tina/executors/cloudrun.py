"""Cloud Run executor: one job execution per work item, against the same image.

Requires the optional dependency: `pip install tina-cli[cloudrun]`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from tina.config import CloudRunOptions
from tina.executors.base import ExecutorError
from tina.log import get_logger

log = get_logger(__name__)

#: Cloud Run injects the execution name into every job task's environment.
EXECUTION_ENV = "CLOUD_RUN_EXECUTION"

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
    ) -> None:
        # Required keys are enforced by CloudRunOptions at config load, so a
        # misconfigured job fails before the first query runs, not after.
        self.options = options
        self.config_path = str(config_path)
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = _jobs_client()
        return self._client

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
        self.client.run_job(request=request)
        log.info(
            "worker enqueued",
            extra={"track": track, "item": item_id or "", "job": self.job_path},
        )

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
