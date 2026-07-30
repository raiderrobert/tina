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
reflects it. `POST /assignees` is an idempotent *add*, the way the real API
behaves -- a replace would let a second identity silently take a claim, and the
volume demo's `no_action_needed` beat would be a fiction. stdlib only -- the
demo adds no dependency.

Pull requests are real server state, not props. The POST assigns the number,
the GET lists only pull requests that were actually created, and
`/{owner}/{name}/pull/{n}` 404s for every other number -- which is what makes
`verified: true` in the run record a check rather than a rubber stamp.

Three flags shape the backlog. Their defaults are the simple demo's, so
`./demo/record.sh` with no arguments gets exactly the server it always had:

    --issues 10        the ten hand-written issues below; any other count is
                       generated from 4001 up, one component label each
    --pr-start 4900    the pull request counter's seed; the first PR is 4901
    --human-held 0     assign the last N issues to a person, at startup

`./demo/record.sh volume` passes `--issues 1012 --pr-start 6000 --human-held 12`:
1,000 workable bugs across eight component queues, plus twelve a human already
holds that no `no:assignee` query will ever match.
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

#: The eight component queues the volume demo fans out across, one track each.
#: Assigned round-robin over the generated issues, so each gets exactly a
#: 1/8 share and the eight `label:` queries are disjoint by construction.
COMPONENTS = ("api", "web", "auth", "data", "infra", "jobs", "sdk", "cli")

#: The first generated issue number. Above it there is room for both the
#: workable range and the human-held tail without touching the PR numbers.
FIRST_GENERATED = 4001

#: The person who already holds the `--human-held` tail. Not the bot, which is
#: the whole point: `GitHubSource.claim` rejects a claim only when a *different*
#: login holds the item.
HUMAN_LOGIN = "alice"

ISSUE = re.compile(r"^/repos/(?P<repo>[^/]+/[^/]+)/issues/(?P<number>\d+)$")
ASSIGNEES = re.compile(r"^/repos/(?P<repo>[^/]+/[^/]+)/issues/(?P<number>\d+)/assignees$")
PULLS = re.compile(r"^/repos/(?P<repo>[^/]+/[^/]+)/pulls$")
PULL = re.compile(r"^/(?P<repo>[^/]+/[^/]+)/pull/(?P<number>\d+)$")

#: Which labels each issue carries. The hand-written ten are not listed and
#: fall back to `bug`, which is what `label:bug` has always matched them on;
#: generated issues get `bug` plus their component.
labels: dict[int, list[str]] = {}

#: Who each issue is assigned to. Empty until the worker claims it, or until
#: `--human-held` seeds a person at startup.
assigned: dict[int, list[str]] = {}

#: The pull requests this server issued, keyed by number, oldest first.
pulls: dict[int, dict[str, Any]] = {}

#: The pull request numbers start above every issue number, so a reader can
#: never mistake one for the other. Bumped before each POST, so the first pull
#: request the stub issues is one above the seed: #4901 by default.
PR_COUNTER_START = 4900
pr_counter = PR_COUNTER_START

#: One lock over every read *and* write of `assigned`, `pulls` and `pr_counter`.
#: This is a ThreadingHTTPServer serving eight concurrent dispatchers, and both
#: halves matter: two POSTs racing on the counter would issue one number twice
#: and earn `verified: true` for a pull request that does not exist, while an
#: unlocked `/search/issues` builds a 1,012-item list out of `assigned` while
#: workers mutate it -- a miscount at best, `RuntimeError: dictionary changed
#: size during iteration` at worst.
#:
#: Every holder is a plain, non-nesting `with state_lock:` inside one handler
#: branch: the helpers below read the state without taking it, and the socket
#: write happens after the block, so a slow client cannot stall the others.
state_lock = threading.Lock()

#: Who the stub reports as the author of the pull requests it is handed. The
#: real API takes this from the token; `record.sh` exports the same value it
#: gives tina, so the two agree.
BOT_LOGIN = os.environ.get("GITHUB_BOT_LOGIN") or "tina-demo-bot"


def issue_labels(number: int) -> list[str]:
    """The issue's labels. Callers hold `state_lock`."""
    return labels.get(number, ["bug"])


def issue_payload(base_url: str, number: int) -> dict[str, Any]:
    """One issue, in the shape `tina.sources.github.Issue` reads.

    Callers hold `state_lock`: this reads `assigned`.
    """
    return {
        "number": number,
        "title": ISSUES.get(number, "Unknown issue"),
        "body": "Reported by a customer. Reproducible on main.",
        "html_url": f"{base_url}/{REPO}/issues/{number}",
        "labels": [{"name": name} for name in issue_labels(number)],
        "assignees": [{"login": login} for login in assigned.get(number, [])],
    }


def open_pull_request(base_url: str, body: dict[str, Any]) -> dict[str, Any]:
    """Record a new pull request under the next number and return it.

    The caller does not get to pick the number -- the same rule the real API
    plays by, and the reason the agent has to report the URL it was handed.

    Callers hold `state_lock`: the increment and the append are one critical
    section, because a lost update issues one number twice.
    """
    global pr_counter
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


def claim(number: int, assignees: list[str]) -> None:
    """Add assignees that are not already there. Callers hold `state_lock`.

    GitHub's assign endpoint is an idempotent add, not a replace, and the
    difference is load-bearing: under a replace a second login would silently
    displace the first, so an issue a person holds could be taken by the bot and
    `GitHubSource.claim`'s re-read would wrongly succeed.
    """
    holders = assigned.setdefault(number, [])
    holders.extend(login for login in assignees if login not in holders)


def matches(q: str, number: int) -> bool:
    """The assignee and label filters the demo's queries use; other qualifiers match.

    `tina dispatch` sends `no:assignee` and `tina status` sends
    `assignee:<login>` for the same track, so a stub that ignored `q` would
    answer both with the same list and make the two counts meaningless.

    `label:` matters for the same reason at volume: the eight component tracks
    differ only by their label, and a stub that ignored it would hand all eight
    dispatchers the same 1,000 issues.

    Callers hold `state_lock`: this reads `assigned`.
    """
    holders = assigned.get(number, [])
    names = issue_labels(number)
    for token in q.split():
        if token == "no:assignee" and holders:
            return False
        if token.startswith("assignee:") and token.removeprefix("assignee:") not in holders:
            return False
        if token.startswith("label:") and token.removeprefix("label:") not in names:
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
            with state_lock:
                items = [
                    issue_payload(self.base_url, number) for number in ISSUES if matches(q, number)
                ]
            self.respond(200, {"total_count": len(items), "items": items})
            return
        match = ISSUE.match(path)
        if match and match["repo"] == REPO and int(match["number"]) in ISSUES:
            with state_lock:
                payload = issue_payload(self.base_url, int(match["number"]))
            self.respond(200, payload)
            return
        match = PULLS.match(path)
        if match and match["repo"] == REPO:
            # Newest last, which is the order they were created in.
            with state_lock:
                listing = list(pulls.values())
            self.respond(200, listing)
            return
        match = PULL.match(path)
        if match and match["repo"] == REPO:
            # The artifact the agent reports. `tina.verify` GETs it, and a 200 is
            # only reachable for a pull request the stub really issued -- a
            # number nobody created falls through to the 404 below, so
            # `verified: true` cannot be earned by naming a plausible URL.
            with state_lock:
                pull = pulls.get(int(match["number"]))
            if pull is not None:
                self.respond(200, pull)
                return
        self.respond(404, {"message": "Not Found"})

    def do_POST(self) -> None:  # noqa: N802 -- the BaseHTTPRequestHandler contract
        path = self.path.split("?", 1)[0]
        body = self.read_body()
        match = ASSIGNEES.match(path)
        if match and match["repo"] == REPO:
            number = int(match["number"])
            assignees = [str(login) for login in body.get("assignees", [])]
            # Read-modify-write in one critical section: two claimers adding
            # themselves at once must both be visible to the re-read that follows.
            with state_lock:
                claim(number, assignees)
                payload = issue_payload(self.base_url, number)
            self.respond(201, payload)
            return
        match = PULLS.match(path)
        if match and match["repo"] == REPO:
            with state_lock:
                payload = open_pull_request(self.base_url, body)
            self.respond(201, payload)
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


def generate_issues(count: int) -> None:
    """Replace the hand-written ten with `count` issues numbered from 4001.

    Deterministic and insertion-ordered -- insertion order is dispatch order --
    so two recordings differ only in timing. Titles cycle the ten above and
    component labels cycle `COMPONENTS`, which is what makes the per-component
    queues exactly equal in size when the count divides by eight.
    """
    titles = list(ISSUES.values())
    ISSUES.clear()
    for index in range(count):
        number = FIRST_GENERATED + index
        ISSUES[number] = titles[index % len(titles)]
        labels[number] = ["bug", COMPONENTS[index % len(COMPONENTS)]]


def seed_human_held(count: int) -> None:
    """Assign the last `count` issues to a person, before anything is served.

    These are open `label:bug` issues that the dispatch query's `no:assignee`
    never matches, so the demo's proof that the factory stays off them is the
    ledger rather than luck. One of them is also the only place ADR-004's loser
    path genuinely fires: a claim is rejected when a *different* login holds it.
    """
    for number in list(ISSUES)[-count:] if count else []:
        assigned[number] = [HUMAN_LOGIN]


class Server(ThreadingHTTPServer):
    """One thread per connection, with a backlog eight dispatchers cannot fill.

    `socketserver`'s default `request_queue_size` is 5; eight dispatchers, their
    workers and `progress.py` polling can present more pending connections than
    that at once, and a refused connection surfaces as a `SourceError`, a dead
    worker and a lost ticket. `daemon_threads` is already `ThreadingHTTPServer`'s
    default and is restated here because `record.sh`'s trap depends on it: the
    stub must not outlive the kill waiting on a keep-alive thread.
    """

    daemon_threads = True
    request_queue_size = 64


def main() -> None:
    global pr_counter

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0, help="0 picks a free port.")
    parser.add_argument(
        "--url-file",
        type=Path,
        help="Write the base URL here once listening, for the caller to read.",
    )
    parser.add_argument(
        "--issues",
        type=int,
        default=len(ISSUES),
        help=(
            "How many issues to serve. The default -- and the literal value 10 --"
            " serves the hand-written ten unchanged; any other count is generated."
        ),
    )
    parser.add_argument(
        "--pr-start",
        type=int,
        default=PR_COUNTER_START,
        help="Seed for the pull request counter; the first PR is one above it.",
    )
    parser.add_argument(
        "--human-held",
        type=int,
        default=0,
        help=f"Assign the last N issues to {HUMAN_LOGIN} at startup.",
    )
    args = parser.parse_args()

    if args.issues != len(ISSUES):
        generate_issues(args.issues)
    seed_human_held(args.human_held)
    pr_counter = args.pr_start

    server = Server(("127.0.0.1", args.port), Handler)
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
