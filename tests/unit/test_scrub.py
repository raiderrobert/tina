from __future__ import annotations

import pytest

from tina.scrub import scrub


@pytest.mark.parametrize(
    ("secret", "label"),
    [
        ("ghp_" + "A" * 36, "github-token"),
        ("ghs_" + "b" * 30, "github-token"),
        ("github_pat_" + "x" * 40, "github-fine-grained-pat"),
        ("ATATT3x" + "F" * 40, "atlassian-api-token"),
        ("eyJhbGciOi.eyJzdWIiOiIx.SflKxwRJSMeKKF2QT4fw", "jwt"),
    ],
)
def test_credential_shapes_are_redacted(secret: str, label: str) -> None:
    text = f'{{"tool_result": "GH_TOKEN={secret} and more"}}'

    cleaned = scrub(text)

    assert secret not in cleaned
    assert f"<REDACTED:{label}>" in cleaned
    assert cleaned.startswith('{"tool_result": "GH_TOKEN=')


def test_a_private_key_is_redacted_across_escaped_newlines() -> None:
    key = "-----BEGIN RSA PRIVATE KEY-----\\nMIIE" + "x" * 20 + "\\n-----END RSA PRIVATE KEY-----"

    assert scrub(f"key: {key}") == "key: <REDACTED:private-key>"


def test_basic_auth_keeps_the_keyword_and_drops_the_credential() -> None:
    header = "Authorization: Basic " + "Zm9vOmJhcg==" * 3

    cleaned = scrub(header)

    assert cleaned.startswith("Authorization: Basic ")
    assert cleaned.endswith("<REDACTED:basic-auth-header>")


def test_ordinary_text_is_untouched() -> None:
    text = "Bumped libfoo from 1.2.3 to 2.0.1; see https://example.test/pr/1"

    assert scrub(text) == text
