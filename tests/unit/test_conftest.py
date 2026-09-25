from __future__ import annotations

import os

import pytest

from conftest import clear_adapter_env
from tina import credentials
from tina.sources.github import GitHubSource


@pytest.mark.parametrize(
    "name",
    (
        "GH_TOKEN",
        "GITHUB_TOKEN_COMMAND",
        "TINA_TRACKS_DIR",
        "TINA_ARTIFACTS_DIR",
    ),
)
def test_clean_env_removes_supported_credential_and_path_inputs(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Inherited adapter inputs must not affect tests using fake clients."""
    monkeypatch.setenv(name, "synthetic-inherited-value")

    clear_adapter_env(monkeypatch)

    assert name not in os.environ


def test_clean_env_prevents_an_inherited_helper_from_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN_COMMAND", "inherited-token-helper")

    clear_adapter_env(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    def unexpected_helper(*args: object, **kwargs: object) -> None:
        raise AssertionError("an inherited credential helper must not run")

    monkeypatch.setattr(credentials.subprocess, "run", unexpected_helper)

    source = GitHubSource(repo="acme/api")

    assert source.client.headers["Authorization"] == "Bearer test-token"
