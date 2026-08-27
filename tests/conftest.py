from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from tina.models import WorkItem

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def make_client() -> Callable[[Handler], httpx.Client]:
    """Build an httpx.Client backed by a mock transport. No test touches the network."""

    def factory(handler: Handler) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler))

    return factory


@pytest.fixture
def work_item() -> WorkItem:
    return WorkItem(
        id="VUL-1",
        source="jira",
        title="CVE-2024-0001 in libfoo",
        description="Bump libfoo to 2.0.1",
        url="https://acme.atlassian.net/browse/VUL-1",
    )


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Adapters read credentials from the environment; keep the real ones out."""
    for name in (
        "JIRA_BASE_URL",
        "JIRA_EMAIL",
        "JIRA_API_TOKEN",
        "JIRA_BOT_ACCOUNT_ID",
        "GITHUB_TOKEN",
        "GITHUB_BOT_LOGIN",
        "GITHUB_API_URL",
        "TINA_HARNESS_TIMEOUT",
        "TINA_CONTROL",
        "TINA_CONTROL_INLINE",
    ):
        monkeypatch.delenv(name, raising=False)
