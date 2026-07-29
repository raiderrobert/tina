"""Cloud Run executor: one job execution per work item, against the same image.

Requires the optional dependency: `pip install tina-cli[cloudrun]`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tina.config import CloudRunOptions
from tina.executors.base import ExecutorError
from tina.log import get_logger

log = get_logger(__name__)

INSTALL_HINT = (
    "the cloudrun executor needs google-cloud-run; install tina with the extra: "
    "`uv add 'tina-cli[cloudrun]'` or `pip install 'tina-cli[cloudrun]'`"
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

    def enqueue(self, workflow: str, item_id: str) -> None:
        run_v2 = _run_v2()
        args = [
            "run",
            "--workflow",
            workflow,
            "--item",
            item_id,
            "--config",
            self.config_path,
        ]
        request = run_v2.RunJobRequest(
            name=self.job_path,
            overrides=run_v2.RunJobRequest.Overrides(
                container_overrides=[run_v2.RunJobRequest.Overrides.ContainerOverride(args=args)]
            ),
        )
        self.client.run_job(request=request)
        log.info(
            "worker enqueued",
            extra={"workflow": workflow, "item": item_id, "job": self.job_path},
        )


def _run_v2() -> Any:
    try:
        # ty: ignore[unresolved-import]  # only present with the `cloudrun` extra
        from google.cloud import run_v2
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ExecutorError(INSTALL_HINT) from exc
    return run_v2


def _jobs_client() -> Any:
    return _run_v2().JobsClient()
