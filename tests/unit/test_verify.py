from __future__ import annotations

import base64

import httpx
import pytest

from tina import verify
from tina.models import Artifact, OutcomeReport, OutcomeStatus


def report(status: OutcomeStatus, *urls: str) -> OutcomeReport:
    return OutcomeReport(
        outcome=status,
        artifacts=[Artifact(kind="github:pr", url=url) for url in urls],
    )


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200)


def test_resolved_with_reachable_artifacts_is_verified() -> None:
    result = verify.verify(report(OutcomeStatus.RESOLVED, "https://example.test/pr/1"), client(ok))

    assert result.verified is True
    assert result.effective_status is OutcomeStatus.RESOLVED


def test_a_missing_artifact_flips_the_effective_status() -> None:
    """The agent's report is preserved; only the status a human sees changes."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    result = verify.verify(
        report(OutcomeStatus.RESOLVED, "https://example.test/pr/1"), client(handler)
    )

    assert result.verified is False
    assert result.outcome is OutcomeStatus.RESOLVED
    assert result.effective_status is OutcomeStatus.NEEDS_HUMAN


def test_one_bad_artifact_among_several_fails_the_check() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200 if request.url.path.endswith("1") else 404)

    result = verify.verify(
        report(OutcomeStatus.RESOLVED, "https://example.test/pr/1", "https://example.test/pr/2"),
        client(handler),
    )

    assert result.verified is False


def test_a_redirect_counts_as_existing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://example.test/elsewhere"})

    result = verify.verify(
        report(OutcomeStatus.RESOLVED, "https://example.test/pr/1"), client(handler)
    )

    assert result.verified is True


def test_a_network_error_counts_as_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    result = verify.verify(
        report(OutcomeStatus.RESOLVED, "https://example.test/pr/1"), client(handler)
    )

    assert result.verified is False
    assert result.effective_status is OutcomeStatus.NEEDS_HUMAN


@pytest.mark.parametrize(
    "status",
    [OutcomeStatus.NO_ACTION_NEEDED, OutcomeStatus.NEEDS_HUMAN, OutcomeStatus.FAILED],
)
def test_only_resolved_is_checked(status: OutcomeStatus) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not fetch anything")

    result = verify.verify(report(status, "https://example.test/pr/1"), client(handler))

    assert result.verified is None
    assert result.effective_status is status


def test_resolved_without_artifacts_has_nothing_to_check() -> None:
    result = verify.verify(report(OutcomeStatus.RESOLVED), client(ok))

    assert result.verified is None


def test_github_urls_get_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")

    assert verify.auth_headers("https://github.com/acme/api/pull/7") == {
        "Authorization": "Bearer ghp_secret"
    }
    assert verify.auth_headers("https://api.github.com/repos/acme/api") == {
        "Authorization": "Bearer ghp_secret"
    }


def test_jira_base_url_host_gets_basic_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_BASE_URL", "https://acme.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "bot@acme.test")
    monkeypatch.setenv("JIRA_API_TOKEN", "tok")

    expected = base64.b64encode(b"bot@acme.test:tok").decode()
    assert verify.auth_headers("https://acme.atlassian.net/browse/VUL-1") == {
        "Authorization": f"Basic {expected}"
    }
    assert verify.auth_headers("https://other.atlassian.net/browse/X-1") == {}


def test_unknown_hosts_are_fetched_anonymously(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")

    assert verify.auth_headers("https://confluence.acme.test/page/1") == {}


def test_a_lookalike_host_does_not_get_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")

    assert verify.auth_headers("https://github.com.evil.test/acme/api") == {}


# --- web URLs are checked through the API they describe -----------------------


@pytest.mark.parametrize(
    ("web", "api"),
    [
        (
            "https://github.com/acme/api/pull/7",
            "https://api.github.com/repos/acme/api/issues/7",
        ),
        (
            "https://github.com/acme/api/issues/629",
            "https://api.github.com/repos/acme/api/issues/629",
        ),
        (
            "https://github.com/acme/api/issues/629#issuecomment-5575080102",
            "https://api.github.com/repos/acme/api/issues/comments/5575080102",
        ),
        (
            "https://github.com/acme/api/pull/7#issuecomment-1",
            "https://api.github.com/repos/acme/api/issues/comments/1",
        ),
        (
            "https://github.com/acme/api/pull/7#discussion_r99",
            "https://api.github.com/repos/acme/api/pulls/comments/99",
        ),
        (
            "https://github.com/acme/api/commit/0123abcd",
            "https://api.github.com/repos/acme/api/commits/0123abcd",
        ),
        (
            "https://github.com/acme/api/releases/tag/v1.2.3",
            "https://api.github.com/repos/acme/api/releases/tags/v1.2.3",
        ),
        ("https://github.com/acme/api", "https://api.github.com/repos/acme/api"),
    ],
)
def test_github_web_urls_map_to_the_api(web: str, api: str) -> None:
    assert verify.api_url(web) == api


def test_unrecognized_github_paths_pass_through() -> None:
    url = "https://github.com/acme/api/blob/main/README.md"
    assert verify.api_url(url) == url


def test_github_enterprise_uses_the_configured_api_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_API_URL", "https://ghe.example/api/v3")
    assert (
        verify.api_url("https://github.com/acme/api/pull/7")
        == "https://ghe.example/api/v3/repos/acme/api/issues/7"
    )


def test_jira_browse_maps_to_the_issue_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_BASE_URL", "https://acme.atlassian.net")
    assert (
        verify.api_url("https://acme.atlassian.net/browse/VUL-1")
        == "https://acme.atlassian.net/rest/api/3/issue/VUL-1"
    )
    other = "https://other.atlassian.net/browse/VUL-1"
    assert verify.api_url(other) == other, "only the configured site is ours to translate"


def test_verification_fetches_the_api_resource_with_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A private repository's pull request page is a 404 to any token; the
    API resource is what the credentials reach."""
    monkeypatch.setenv("GH_TOKEN", "ghp_x")
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers.get("Authorization", "")))
        return httpx.Response(200 if request.url.host == "api.github.com" else 404)

    result = verify.verify(
        report(OutcomeStatus.RESOLVED, "https://github.com/acme/api/pull/7"), client(handler)
    )

    assert result.verified is True
    assert seen == [("https://api.github.com/repos/acme/api/issues/7", "Bearer ghp_x")]


def test_gh_token_is_accepted_for_github_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "ghp_cli")
    assert verify.auth_headers("https://api.github.com/repos/a/b") == {
        "Authorization": "Bearer ghp_cli"
    }


def test_verification_re_mints_a_github_token_on_401(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    script = tmp_path / "mint.py"
    script.write_text("print('ghs_fresh')")
    monkeypatch.setenv("GITHUB_TOKEN_COMMAND", f"{__import__('sys').executable} {script}")
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_stale")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Authorization", ""))
        return httpx.Response(200 if seen[-1] == "Bearer ghs_fresh" else 401)

    result = verify.verify(
        report(OutcomeStatus.RESOLVED, "https://github.com/acme/api/pull/7"), client(handler)
    )

    assert result.verified is True
    assert seen == ["Bearer ghs_stale", "Bearer ghs_fresh"]
