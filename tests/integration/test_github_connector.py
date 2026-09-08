"""`tina-source-github` as Tina runs it: a process, spoken to over stdio,
against a GitHub that is a local HTTP server with canned answers.

The connector is started exactly as the console script would be
(`python -m tina.connectors.github`), pointed at the fake with
`GITHUB_API_URL`, and driven by the real `ConnectorClient` — so this covers
the whole path from Tina's `Source` call to the connector's HTTP request.
"""

from __future__ import annotations

import json
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from tina.connector.client import ConnectorClient, OptionsRejected
from tina.connector.protocol import Lifecycle
from tina.models import WorkItem

REPO = "acme/api"


def issue(number: int, labels: list[str], state: str = "open") -> dict[str, Any]:
    return {
        "number": number,
        "title": f"issue {number}",
        "body": "body",
        "state": state,
        "html_url": f"https://github.com/{REPO}/issues/{number}",
        "assignees": [],
        "labels": [{"name": name} for name in labels],
    }


class FakeGitHub(BaseHTTPRequestHandler):
    """Just enough of the REST API for a read-only run: user, search, one issue."""

    requests: list[str] = []
    issues = {42: issue(42, ["needs-triage"]), 43: issue(43, ["needs-triage", "bot-claimed"])}

    def do_GET(self) -> None:  # noqa: N802 — http.server's contract
        FakeGitHub.requests.append(f"GET {self.path}")
        if self.path == "/user":
            self._json({"login": "acme-bot"})
        elif self.path.startswith("/search/issues"):
            matched = [
                i
                for n, i in self.issues.items()
                if "bot-claimed" not in {x["name"] for x in i["labels"]}
            ]
            self._json({"items": matched})
        elif self.path.startswith(f"/repos/{REPO}/issues/"):
            number = int(self.path.rsplit("/", 1)[1])
            if number in self.issues:
                self._json(self.issues[number])
            else:
                self._json({"message": "Not Found"}, 404)
        else:
            self._json({"message": "Not Found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        FakeGitHub.requests.append(f"POST {self.path}")
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path.endswith("/labels"):
            number = int(self.path.split("/")[-2])
            self.issues[number]["labels"] += [{"name": n} for n in body["labels"]]
            self._json(self.issues[number]["labels"])
        else:
            self._json({}, 201)

    def _json(self, payload: Any, status: int = 200) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:  # quiet
        pass


@pytest.fixture
def github() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    FakeGitHub.requests = []
    FakeGitHub.issues = {
        42: issue(42, ["needs-triage"]),
        43: issue(43, ["needs-triage", "bot-claimed"]),
    }
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def connector(api: str, options: dict[str, Any] | None = None) -> ConnectorClient:
    return ConnectorClient(
        [sys.executable, "-m", "tina.connectors.github"],
        name="github",
        track="triage",
        options={"repo": REPO, "labels": ["needs-triage"]} if options is None else options,
        lifecycle=Lifecycle(claim="label", claim_label="bot-claimed", blocked_label="bot-blocked"),
        env={"GITHUB_API_URL": api, "GITHUB_TOKEN": "ghp_test", "PATH": "/usr/bin:/bin"},
    )


def test_the_github_connector_works_end_to_end_over_the_pipe(github: str) -> None:
    with connector(github) as c:
        assert c.connector["name"] == "github"
        assert c.login() == "acme-bot"

        query = c.build_query()
        assert query == (
            'repo:acme/api is:issue is:open no:assignee label:"needs-triage"'
            ' -label:"bot-blocked" -label:"bot-claimed"'
        )

        items = c.query(query)
        assert [i.id for i in items] == ["acme/api#42"], "the fake filters the claimed one out"
        assert isinstance(items[0], WorkItem)

        assert c.get("acme/api#42").title == "issue 42"
        assert c.matches("acme/api#42", query) is True
        assert c.matches("acme/api#43", query) is False, "carries the claim label"

        assert c.claim(items[0]) is True
        assert "POST /repos/acme/api/issues/42/labels" in FakeGitHub.requests
        assert c.claim(items[0]) is False, "already carries the label now"


def test_verify_artifact_checks_through_the_api_with_the_connectors_credentials(
    github: str,
) -> None:
    with connector(github) as c:
        assert c.verify_artifact("https://github.com/acme/api/issues/42") is True
        assert c.verify_artifact("https://github.com/acme/api/issues/999") is False
        assert c.verify_artifact("https://example.test/elsewhere") is None
    # The API request went to the fake, authenticated by the connector, not by tina.
    assert f"GET /repos/{REPO}/issues/42" in FakeGitHub.requests


def test_bad_options_are_refused_before_any_http(github: str) -> None:
    with pytest.raises(OptionsRejected, match=r"\[triage\.options\]: repo: Field required"):
        connector(github, options={"repos": [REPO]}).login()
    assert FakeGitHub.requests == []
