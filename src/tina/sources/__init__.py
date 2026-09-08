"""Source adapters: where work items come from, and how they get claimed."""

from __future__ import annotations

import httpx

from tina.config import Config, TrackConfig
from tina.sources.base import Source
from tina.sources.github import GitHubSource
from tina.sources.jira import JiraSource

__all__ = ["Source", "JiraSource", "GitHubSource", "build"]


def build(
    track: TrackConfig, client: httpx.Client | None = None, config: Config | None = None
) -> Source:
    """Instantiate the source a track declares.

    A `[sources.<name>]` table in the config names a connector process
    (ADR-019); the client for it satisfies the same protocol as the in-process
    adapters, so callers cannot tell which they got. Without one, the built-in
    adapter of that name.
    """
    if track.source is None:
        raise ValueError(f'track {track.name!r} has no source (mode = "sweep")')
    connector = config.connector(track) if config is not None else None
    if connector is not None:
        from tina.connector.client import ConnectorClient
        from tina.connector.protocol import Lifecycle

        return ConnectorClient(
            connector.command,
            name=connector.name,
            track=track.name,
            options=track.options,
            lifecycle=Lifecycle(
                claim=track.claim, claim_label=track.claim_label, blocked_label=track.blocked_label
            ),
            timeout=connector.timeout,
        )
    if track.source == "jira":
        return JiraSource(
            client=client,
            blocked_label=track.blocked_label,
            claim_policy=track.claim,
            claim_label=track.claim_label,
            claim_transition=track.claim_transition,
            blocked_transition=track.blocked_transition,
        )
    if track.source == "github":
        return GitHubSource(
            repo=track.repo or "",
            client=client,
            blocked_label=track.blocked_label,
            claim_policy=track.claim,
            claim_label=track.claim_label,
        )
    raise ValueError(f"unknown source {track.source!r}")
