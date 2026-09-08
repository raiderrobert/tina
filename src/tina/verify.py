"""Generic artifact verification.

When the agent reports `resolved` with artifacts, Tina GETs each URL using
credentials already in the environment. This catches the dominant failure — an
agent reporting `resolved` with a PR URL it never opened.

An agent reports the URL a human would click — `github.com/.../pull/7`,
`acme.atlassian.net/browse/VUL-1`. Those are web pages, and a tracker's web
pages do not honor API credentials: a private repository's pull request is a
404 to anyone but a browser session, token or no token. So before fetching,
known web URL shapes are translated to the API resource that describes the
same thing, which the credentials do reach. Anything unrecognized is fetched
as given. Still one GET per artifact, still existence only (ADR-007).

The agent's report is never overwritten. A failed check records
`verified: false` alongside it, which flips the effective status to
`needs_human` (architecture §14).
"""

from __future__ import annotations

import base64
import os
import re
from urllib.parse import urlsplit

import httpx

from tina import credentials
from tina.log import get_logger
from tina.models import OutcomeReport, OutcomeStatus

log = get_logger(__name__)

TIMEOUT = 30.0


def verify(report: OutcomeReport, client: httpx.Client | None = None) -> OutcomeReport:
    """Set `verified` on the report when there is something to check.

    The other three outcomes, and `resolved` with no artifacts, have nothing to
    check and leave `verified` as None.
    """
    if report.outcome is not OutcomeStatus.RESOLVED or not report.artifacts:
        return report

    owned = client is None
    client = client or httpx.Client(timeout=TIMEOUT, follow_redirects=False)
    try:
        report.verified = all(_exists(client, str(artifact.url)) for artifact in report.artifacts)
    finally:
        if owned:
            client.close()
    return report


def _exists(client: httpx.Client, url: str) -> bool:
    target = api_url(url)
    try:
        response = client.get(target, headers=auth_headers(target))
        if response.status_code == 401 and _refresh_github_token(target):
            response = client.get(target, headers=auth_headers(target))
    except httpx.HTTPError as exc:
        # A network error is a failed check, not an excuse to skip one.
        log.warning("artifact unreachable", extra={"url": url, "error": str(exc)})
        return False
    ok = 200 <= response.status_code < 400
    if not ok:
        log.warning(
            "artifact missing",
            extra={"url": url, "checked": target, "status": response.status_code},
        )
    return ok


#: `github.com/{owner}/{repo}/...` web paths and the API resource for each. A
#: comment anchor on an issue or pull request page names the comment, which is
#: the artifact the agent means. Ordered so the anchored shapes match first.
_GITHUB_WEB = (
    (re.compile(r"^/([^/]+)/([^/]+)/(?:pull|issues)/\d+$"), "/repos/{0}/{1}/issues/{n}"),
    (re.compile(r"^/([^/]+)/([^/]+)/commit/([0-9a-f]{7,40})$"), "/repos/{0}/{1}/commits/{2}"),
    (re.compile(r"^/([^/]+)/([^/]+)/releases/tag/([^/]+)$"), "/repos/{0}/{1}/releases/tags/{2}"),
    (re.compile(r"^/([^/]+)/([^/]+)/?$"), "/repos/{0}/{1}"),
)
_GITHUB_NUMBER = re.compile(r"/(?:pull|issues)/(\d+)$")
_GITHUB_COMMENT = re.compile(r"^issuecomment-(\d+)$")
_GITHUB_REVIEW_COMMENT = re.compile(r"^discussion_r(\d+)$")
_JIRA_BROWSE = re.compile(r"^/browse/([A-Z][A-Z0-9_]*-\d+)$")


def api_url(url: str) -> str:
    """The API resource behind a tracker web URL, or the URL itself.

    GitHub: `github.com/o/r/pull/7` → `api.github.com/repos/o/r/issues/7` (a
    pull request is an issue to the issues API, and the check is existence);
    `…#issuecomment-N` → the comment; a commit, a release tag, the repository.
    `GITHUB_API_URL` swaps the API host for GitHub Enterprise. Jira:
    `<base>/browse/KEY-1` → `<base>/rest/api/3/issue/KEY-1`, when the host is
    the configured `JIRA_BASE_URL`. Every other URL passes through untouched.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host in {"github.com", "www.github.com"}:
        return _github_api(parts.path, parts.fragment) or url
    jira_base = os.environ.get("JIRA_BASE_URL", "")
    jira_host = (urlsplit(jira_base).hostname or "").lower()
    if jira_host and host == jira_host:
        match = _JIRA_BROWSE.match(parts.path)
        if match:
            return f"{jira_base.rstrip('/')}/rest/api/3/issue/{match.group(1)}"
    return url


def _github_api(path: str, fragment: str) -> str | None:
    base = (os.environ.get("GITHUB_API_URL") or "https://api.github.com").rstrip("/")
    path = path.rstrip("/")
    for pattern, template in _GITHUB_WEB:
        match = pattern.match(path)
        if not match:
            continue
        owner, repo = match.group(1), match.group(2)
        comment = _GITHUB_COMMENT.match(fragment)
        if comment:
            return f"{base}/repos/{owner}/{repo}/issues/comments/{comment.group(1)}"
        review = _GITHUB_REVIEW_COMMENT.match(fragment)
        if review:
            return f"{base}/repos/{owner}/{repo}/pulls/comments/{review.group(1)}"
        number = _GITHUB_NUMBER.search(path)
        return base + template.format(*match.groups(), n=number.group(1) if number else "")
    return None


def _refresh_github_token(url: str) -> bool:
    """Re-mint a short-lived GitHub token into the environment, when the
    deployment configured a command for one and the URL is GitHub's. True
    when a fresh token was placed, so the caller retries once."""
    command = credentials.token_command(credentials.GITHUB_TOKEN_COMMAND)
    if command is None or not _is_github(url):
        return False
    os.environ["GITHUB_TOKEN"] = credentials.run_token_command(
        command, credentials.GITHUB_TOKEN_COMMAND
    )
    return True


def _is_github(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    api_host = (
        urlsplit(os.environ.get("GITHUB_API_URL") or "https://api.github.com").hostname or ""
    ).lower()
    return _matches(host, "github.com") or host == api_host


def auth_headers(url: str) -> dict[str, str]:
    """Best-effort credentials for a result system, from the environment.

    Tina needs read access to systems it never writes to. Those credentials are
    already in the image for the agent, so this is env reuse rather than new
    secrets plumbing. Anything unrecognized is fetched anonymously.
    """
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return {}

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and _is_github(url):
        return {"Authorization": f"Bearer {token}"}

    jira_base = os.environ.get("JIRA_BASE_URL")
    email = os.environ.get("JIRA_EMAIL")
    api_token = os.environ.get("JIRA_API_TOKEN")
    if jira_base and email and api_token:
        jira_host = (urlsplit(jira_base).hostname or "").lower()
        if jira_host and host == jira_host:
            return {"Authorization": _basic(email, api_token)}

    return {}


def _matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def _basic(user: str, password: str) -> str:
    encoded = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    return f"Basic {encoded}"
