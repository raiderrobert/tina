from __future__ import annotations

import sys

import pytest

from tina import credentials


def test_unset_means_no_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(credentials.GITHUB_TOKEN_COMMAND, raising=False)
    assert credentials.token_command(credentials.GITHUB_TOKEN_COMMAND) is None
    monkeypatch.setenv(credentials.GITHUB_TOKEN_COMMAND, "   ")
    assert credentials.token_command(credentials.GITHUB_TOKEN_COMMAND) is None


def test_the_command_is_split_like_a_shell_would(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(credentials.GITHUB_TOKEN_COMMAND, 'helper token --scope "a b"')
    assert credentials.token_command(credentials.GITHUB_TOKEN_COMMAND) == [
        "helper",
        "token",
        "--scope",
        "a b",
    ]


def test_the_token_is_the_commands_stripped_stdout() -> None:
    token = credentials.run_token_command(
        [sys.executable, "-c", "print('  ghs_fresh\\n')"], credentials.GITHUB_TOKEN_COMMAND
    )
    assert token == "ghs_fresh"


def test_a_failing_command_is_loud() -> None:
    with pytest.raises(credentials.CredentialError, match="produced no token: nope"):
        credentials.run_token_command(
            [sys.executable, "-c", "import sys; sys.stderr.write('nope'); sys.exit(3)"],
            credentials.GITHUB_TOKEN_COMMAND,
        )


def test_an_empty_answer_is_loud() -> None:
    with pytest.raises(credentials.CredentialError, match="no output"):
        credentials.run_token_command(
            [sys.executable, "-c", "pass"], credentials.GITHUB_TOKEN_COMMAND
        )


def test_a_missing_binary_is_loud() -> None:
    with pytest.raises(credentials.CredentialError, match="could not run"):
        credentials.run_token_command(["no-such-token-helper"], credentials.GITHUB_TOKEN_COMMAND)
