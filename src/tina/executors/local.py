"""Local executor: run each worker as a blocking subprocess, one after another.

Not optional — this is how anyone tries Tina without cloud infrastructure.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tina.executors.base import ExecutorError
from tina.log import get_logger

log = get_logger(__name__)


class LocalExecutor:
    """Sequential subprocess fan-out."""

    name = "local"

    def __init__(self, config_path: Path | str) -> None:
        self.config_path = Path(config_path)

    def enqueue(self, track: str, item_id: str) -> None:
        command = [
            sys.executable,
            "-m",
            "tina",
            "run",
            "--track",
            track,
            "--item",
            item_id,
            "--config",
            str(self.config_path),
        ]
        log.info("worker starting", extra={"track": track, "item": item_id})
        try:
            completed = subprocess.run(command, check=False)
        except OSError as exc:
            raise ExecutorError(f"local executor could not start a worker: {exc}") from exc
        log.info(
            "worker finished",
            extra={"track": track, "item": item_id, "exit_code": completed.returncode},
        )
