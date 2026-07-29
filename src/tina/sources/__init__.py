"""Source adapters: where work items come from, and how they get claimed."""

from __future__ import annotations

import httpx

from tina.config import WorkflowConfig
from tina.sources.base import Source
from tina.sources.github import GitHubSource
from tina.sources.jira import JiraSource

__all__ = ["Source", "JiraSource", "GitHubSource", "build"]


def build(workflow: WorkflowConfig, client: httpx.Client | None = None) -> Source:
    """Instantiate the source adapter a workflow declares."""
    if workflow.source == "jira":
        return JiraSource(client=client)
    if workflow.source == "github":
        return GitHubSource(repo=workflow.repo or "", client=client)
    raise ValueError(f"unknown source {workflow.source!r}")
