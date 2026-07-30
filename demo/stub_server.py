"""A local stand-in for the GitHub REST API, so the README demo is hermetic.

`tina run` claims a work item by POSTing to the real tracker, so recording a
demo against a live repo would mutate it. The demo points `GITHUB_API_URL` at
this server instead and serves the endpoints the recording touches:

    GET  /search/issues                         -- the dispatch query, filtered by assignee
    GET  /repos/{owner}/{name}/issues/{n}       -- fetch, and the claim re-read
    POST /repos/{owner}/{name}/issues/{n}/assignees
    POST /repos/{owner}/{name}/pulls            -- the agent opens one pull request per ticket
    GET  /repos/{owner}/{name}/pulls            -- the review queue `queue.sh prs` reads
    GET  /{owner}/{name}/pull/{n}               -- the artifact tina verifies

Claiming is assign-then-reread (`GitHubSource.claim`): the bot must come back
as the *sole* assignee, so the POST records the assignment and the next GET
reflects it. stdlib only -- the demo adds no dependency.

Pull requests are real server state, not props. The POST assigns the number,
the GET lists only pull requests that were actually created, and
`/{owner}/{name}/pull/{n}` 404s for every other number -- which is what makes
`verified: true` in the run record a check rather than a rubber stamp.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

REPO = "acme/api"

#: The issues the dispatch query matches. Insertion order is dispatch order and
#: is what makes the recording reproducible, so do not sort this at use.
ISSUES: dict[int, str] = {
    4821: "Crash on empty webhook payload",
    4830: "Retry storm when the upstream returns 503",
    4844: "Timestamps drift by one hour under DST",
    4852: "Duplicate charge when a retry races the callback",
    4858: "Search returns deleted records after a reindex",
    4863: "CSV export truncates fields containing a comma",
    4871: "Token refresh loops when the clock is skewed",
    4877: "Pagination skips a row at the page boundary",
    4884: "Uploads over 10 MB fail without an error",
    4890: "Rate limiter counts preflight requests twice",
}

ISSUE = re.compile(r"^/repos/(?P<repo>[^/]+/[^/]+)/issues/(?P<number>\d+)$")
ASSIGNEES = re.compile(r"^/repos/(?P<repo>[^/]+/[^/]+)/issues/(?P<number>\d+)/assignees$")
PULLS = re.compile(r"^/repos/(?P<repo>[^/]+/[^/]+)/pulls$")
PULL = re.compile(r"^/(?P<repo>[^/]+/[^/]+)/pull/(?P<number>\d+)$")

#: Who each issue is assigned to. Empty until the worker claims it.
assigned: dict[int, list[str]] = {}

#: The pull requests this server issued, keyed by number, oldest first.
pulls: dict[int, dict[str, Any]] = {}

#: The pull request numbers start above every issue number, so a reader can
#: never mistake one for the other. Bumped before each POST, so the first pull
#: request the stub issues is #4901.
PR_COUNTER_START = 4900
pr_counter = PR_COUNTER_START

#: This is a ThreadingHTTPServer: two POSTs can land at once, and each must get
#: its own number.
pr_lock = threading.Lock()

#: Who the stub reports as the author of the pull requests it is handed. The
#: real API takes this from the token; `record.sh` exports the same value it
#: gives tina, so the two agree.
BOT_LOGIN = os.environ.get("GITHUB_BOT_LOGIN") or "tina-demo-bot"


def issue_payload(base_url: str, number: int) -> dict[str, Any]:
    """One issue, in the shape `tina.sources.github.Issue` reads."""
    return {
        "number": number,
        "title": ISSUES.get(number, "Unknown issue"),
        "body": "Reported by a customer. Reproducible on main.",
        "html_url": f"{base_url}/{REPO}/issues/{number}",
        "assignees": [{"login": login} for login in assigned.get(number, [])],
    }


def open_pull_request(base_url: str, body: dict[str, Any]) -> dict[str, Any]:
    """Record a new pull request under the next number and return it.

    The caller does not get to pick the number -- the same rule the real API
    plays by, and the reason the agent has to report the URL it was handed.
    """
    global pr_counter
    with pr_lock:
        pr_counter += 1
        number = pr_counter
        payload = {
            "number": number,
            "title": str(body.get("title", "")),
            "body": str(body.get("body", "")),
            "html_url": f"{base_url}/{REPO}/pull/{number}",
            "state": "open",
            "user": {"login": BOT_LOGIN},
        }
        pulls[number] = payload
    return payload


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
        match = PULLS.match(path)
        if match and match["repo"] == REPO:
            # Newest last, which is the order they were created in.
            self.respond(200, list(pulls.values()))
            return
        match = PULL.match(path)
        if match and match["repo"] == REPO and int(match["number"]) in pulls:
            # The artifact the agent reports. `tina.verify` GETs it, and this
            # 200 is only reachable for a pull request the stub really issued --
            # a number nobody created falls through to the 404 below, so
            # `verified: true` cannot be earned by naming a plausible URL.
            self.respond(200, pulls[int(match["number"])])
            return
        self.respond(404, {"message": "Not Found"})

    def do_POST(self) -> None:  # noqa: N802 -- the BaseHTTPRequestHandler contract
        path = self.path.split("?", 1)[0]
        body = self.read_body()
        match = ASSIGNEES.match(path)
        if match and match["repo"] == REPO:
            number = int(match["number"])
            assigned[number] = list(body.get("assignees", []))
            self.respond(201, issue_payload(self.base_url, number))
            return
        match = PULLS.match(path)
        if match and match["repo"] == REPO:
            self.respond(201, open_pull_request(self.base_url, body))
            return
        self.respond(404, {"message": "Not Found"})

    def read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        parsed = json.loads(self.rfile.read(length) or b"{}")
        return parsed if isinstance(parsed, dict) else {}

    def respond(self, status: int, payload: dict[str, Any] | list[Any]) -> None:
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
