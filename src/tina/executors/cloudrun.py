"""Cloud Run executor: one job execution per work item, against the same image.

Requires the optional dependency: `pip install tina[cloudrun]`.
"""

from __future__ import annotations

from typing import Any

from tina.executors.base import ExecutorError
from tina.log import get_logger

log = get_logger(__name__)

INSTALL_HINT = (
    "the cloudrun executor needs google-cloud-run; install tina with the extra: "
    "`uv add 'tina[cloudrun]'` or `pip install 'tina[cloudrun]'`"
)


class CloudRunExecutor:
    """Create a Cloud Run job execution with per-item args overrides."""

    name = "cloudrun"

    def __init__(
        self,
        project: str,
        region: str,
        job: str,
        config_path: str = "tina.toml",
        client: Any = None,
    ) -> None:
        self.project = project
        self.region = region
        self.job = job
        self.config_path = config_path
        self._client = client

    @classmethod
    def from_config(cls, options: dict[str, Any], config_path: str) -> CloudRunExecutor:
        missing = [key for key in ("project", "region", "job") if not options.get(key)]
        if missing:
            raise ExecutorError(
                "[executors.cloudrun] is missing required keys: " + ", ".join(missing)
            )
        return cls(
            project=options["project"],
            region=options["region"],
            job=options["job"],
            config_path=config_path,
        )

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = _jobs_client()
        return self._client

    @property
    def job_path(self) -> str:
        return f"projects/{self.project}/locations/{self.region}/jobs/{self.job}"

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
