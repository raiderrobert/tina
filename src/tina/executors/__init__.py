"""Executor adapters: how the dispatcher enqueues workers."""

from __future__ import annotations

from tina.config import Config
from tina.executors.base import Executor, ExecutorError
from tina.executors.cloudrun import CloudRunExecutor
from tina.executors.local import LocalExecutor

__all__ = ["Executor", "ExecutorError", "LocalExecutor", "CloudRunExecutor", "build"]


def build(config: Config) -> Executor:
    """Instantiate the executor a config selects."""
    if config.executor == "local":
        return LocalExecutor(config_path=config.path)
    if config.executor == "cloudrun":
        return CloudRunExecutor.from_config(config.executor_config(), str(config.path))
    raise ExecutorError(f"unknown executor {config.executor!r}")
