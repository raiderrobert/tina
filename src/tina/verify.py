"""Generic artifact verification.

When the agent reports `resolved` with artifacts, Tina GETs each URL using
credentials already in the environment. This catches the dominant failure — an
agent reporting `resolved` with a PR URL it never opened.

The agent's report is never overwritten. A failed check records
`verified: false` alongside it, which flips the effective status to
`needs_human` (architecture §14).
"""

from __future__ import annotations

import base64
import os
from urllib.parse import urlsplit

import httpx

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
        report.verified = all(_exists(client, artifact.url) for artifact in report.artifacts)
    finally:
        if owned:
            client.close()
    return report


def _exists(client: httpx.Client, url: str) -> bool:
    try:
        response = client.get(url, headers=auth_headers(url))
    except httpx.HTTPError as exc:
        # A network error is a failed check, not an excuse to skip one.
        log.warning("artifact unreachable", extra={"url": url, "error": str(exc)})
        return False
    ok = 200 <= response.status_code < 400
    if not ok:
        log.warning("artifact missing", extra={"url": url, "status": response.status_code})
    return ok


def auth_headers(url: str) -> dict[str, str]:
    """Best-effort credentials for a result system, from the environment.

    Tina needs read access to systems it never writes to. Those credentials are
    already in the image for the agent, so this is env reuse rather than new
    secrets plumbing. Anything unrecognized is fetched anonymously.
    """
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return {}

    token = os.environ.get("GITHUB_TOKEN")
    if token and _matches(host, "github.com"):
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
