"""A local stand-in for the GitHub REST API, so the README demo is hermetic.

`tina run` claims a work item by POSTing to the real tracker, so recording a
demo against a live repo would mutate it. The demo points `GITHUB_API_URL` at
this server instead and serves the four endpoints the run touches:

    GET  /search/issues                         -- the dispatch query, filtered by assignee
    GET  /repos/{owner}/{name}/issues/{n}       -- fetch, and the claim re-read
    POST /repos/{owner}/{name}/issues/{n}/assignees
    GET  /{owner}/{name}/pull/{n}               -- the artifact tina verifies

Claiming is assign-then-reread (`GitHubSource.claim`): the bot must come back
as the *sole* assignee, so the POST records the assignment and the next GET
reflects it. stdlib only -- the demo adds no dependency.
"""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

REPO = "acme/api"

#: The issues the dispatch query matches, newest first.
ISSUES: dict[int, str] = {
    4821: "Crash on empty webhook payload",
    4830: "Retry storm when the upstream returns 503",
    4844: "Timestamps drift by one hour under DST",
}

ISSUE = re.compile(r"^/repos/(?P<repo>[^/]+/[^/]+)/issues/(?P<number>\d+)$")
ASSIGNEES = re.compile(r"^/repos/(?P<repo>[^/]+/[^/]+)/issues/(?P<number>\d+)/assignees$")
PULL = re.compile(r"^/(?P<repo>[^/]+/[^/]+)/pull/(?P<number>\d+)$")

#: Who each issue is assigned to. Empty until the worker claims it.
assigned: dict[int, list[str]] = {}


def issue_payload(base_url: str, number: int) -> dict[str, Any]:
    """One issue, in the shape `tina.sources.github.Issue` reads."""
    return {
        "number": number,
        "title": ISSUES.get(number, "Unknown issue"),
        "body": "Reported by a customer. Reproducible on main.",
        "html_url": f"{base_url}/{REPO}/issues/{number}",
        "assignees": [{"login": login} for login in assigned.get(number, [])],
    }


def matches(q: str, number: int) -> bool:
    """The two assignee filters the demo's queries use; other qualifiers always match.

    `tina dispatch` sends `no:assignee` and `tina status` sends
    `assignee:<login>` for the same track, so a stub that ignored `q` would
    answer both with the same list and make the two counts meaningless.
    """
    holders = assigned.get(number, [])
    for token in q.split():
        if token == "no:assignee" and holders:
            return False
        if token.startswith("assignee:") and token.removeprefix("assignee:") not in holders:
            return False
    return True


class Handler(BaseHTTPRequestHandler):
    """Routes the demo's requests; everything else is a 404."""

    protocol_version = "HTTP/1.1"
    #: Filled in by `main()` once the listening port is known. Issues carry
    #: absolute URLs, and the port is not decided until the socket is bound.
    base_url = ""

    def do_GET(self) -> None:  # noqa: N802 -- the BaseHTTPRequestHandler contract
        path = self.path.split("?", 1)[0]
        if path == "/search/issues":
            q = parse_qs(urlparse(self.path).query).get("q", [""])[0]
            items = [
                issue_payload(self.base_url, number) for number in ISSUES if matches(q, number)
            ]
            self.respond(200, {"total_count": len(items), "items": items})
            return
        match = ISSUE.match(path)
        if match and match["repo"] == REPO and int(match["number"]) in ISSUES:
            self.respond(200, issue_payload(self.base_url, int(match["number"])))
            return
        if PULL.match(path):
            # The artifact the agent reports. `tina.verify` GETs it, and a 200
            # here is what makes `verified: true` a real result.
            self.respond(200, {"state": "open"})
            return
        self.respond(404, {"message": "Not Found"})

    def do_POST(self) -> None:  # noqa: N802 -- the BaseHTTPRequestHandler contract
        match = ASSIGNEES.match(self.path.split("?", 1)[0])
        if not match or match["repo"] != REPO:
            self.respond(404, {"message": "Not Found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        number = int(match["number"])
        assigned[number] = list(body.get("assignees", []))
        self.respond(201, issue_payload(self.base_url, number))

    def respond(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        """Silence the per-request log; the recording shows tina, not this."""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0, help="0 picks a free port.")
    parser.add_argument(
        "--url-file",
        type=Path,
        help="Write the base URL here once listening, for the caller to read.",
    )
    args = parser.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    base_url = f"http://127.0.0.1:{server.server_port}"
    Handler.base_url = base_url
    if args.url_file:
        args.url_file.write_text(base_url, encoding="utf-8")
    print(f"stub github api listening on {base_url}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
