"""Source adapters: where work items come from, and how they get claimed."""

from __future__ import annotations

import httpx

from tina.config import TrackConfig
from tina.sources.base import Source
from tina.sources.github import GitHubSource
from tina.sources.jira import JiraSource

__all__ = ["Source", "JiraSource", "GitHubSource", "build"]


def build(track: TrackConfig, client: httpx.Client | None = None) -> Source:
    """Instantiate the source adapter a track declares."""
    if track.source == "jira":
        return JiraSource(client=client, blocked_label=track.blocked_label)
    if track.source == "github":
        return GitHubSource(repo=track.repo or "", client=client, blocked_label=track.blocked_label)
    raise ValueError(f"unknown source {track.source!r}")
