"""Source adapters: where work items come from, and how they get claimed."""

from __future__ import annotations

import httpx

from tina.config import TrackConfig
from tina.sources import github, jira
from tina.sources.base import Source
from tina.sources.github import GitHubSource
from tina.sources.jira import JiraSource

__all__ = ["Source", "JiraSource", "GitHubSource", "build", "render"]

#: Each source's compiler, for rendering a track's query without credentials.
_COMPILERS = {"jira": jira.compile, "github": github.compile}


def render(track: TrackConfig) -> str:
    """The native query string a track's source would run."""
    if track.source is None:
        raise ValueError(f'track {track.name!r} has no source (mode = "sweep")')
    return _COMPILERS[track.source](track.query)


def build(track: TrackConfig, client: httpx.Client | None = None) -> Source:
    """Instantiate the source adapter a track declares."""
    if track.source is None:
        raise ValueError(f'track {track.name!r} has no source (mode = "sweep")')
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
