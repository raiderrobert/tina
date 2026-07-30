#!/usr/bin/env python3
"""The volume demo's exactly-once proof, machine-checked.

Beat 5 of `volume.sh`. Eleven assertions over the eight dispatchers' JSON log
streams and the stub tracker's own endpoints; **the first violation exits 1**.
`volume.sh` runs under `set -eu` and `asciinema rec --return` propagates the
status, so a broken assertion fails `record.sh` before `agg` runs and no gif is
produced. The closing figures on screen are therefore ones that passed, not ones
that were typed.

Two claims exist to be proved here, because they are the two a viewer has no
way to check for themselves:

- **Every ticket was worked exactly once.** Not by winning a race -- with a
  single bot login `GitHubSource.claim` cannot reject a duplicate worker, since
  a second worker assigning the same bot is still the sole assignee. It comes
  from the eight dispatchers querying disjoint component slices, which is the
  mechanism that provides it in production too. Checks 1, 5, 8 and 9.
- **`verified: true` was computed, not asserted.** `tina.verify` GETs each pull
  request URL back off the tracker, and the stub serves only the numbers it
  issued -- check 10 fetches one it never issued and requires a 404.

It reads the tracker over the endpoints `tina` and `queue.sh` already use. There
is no demo-only introspection endpoint: a check that needed one would be
checking the stub's opinion of itself.

    ./audit.py runs-*.jsonl

stdlib only; reads `GITHUB_API_URL` and `GITHUB_BOT_LOGIN` from the environment.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

REPO = "acme/api"
TIMEOUT = 10.0

#: The shape `record.sh volume` sets up, and the shape this asserts. Every one
#: of these is a consequence of two flags and one rule: the stub serves
#: `--issues 1012 --human-held 12` from 4001 up, and `agent.py` escalates every
#: 30th ticket number (`TINA_DEMO_ESCALATE_EVERY=30`).
FIRST_ITEM = 4001
LAST_ITEM = 5000
TOTAL = LAST_ITEM - FIRST_ITEM + 1
ESCALATE_EVERY = 30
ESCALATED = len(range(FIRST_ITEM + (-FIRST_ITEM % ESCALATE_EVERY), LAST_ITEM + 1, ESCALATE_EVERY))
RESOLVED = TOTAL - ESCALATED
HUMAN_HELD = 12
HUMAN_LOGIN = "alice"
SHARDS = 8
PER_SHARD = TOTAL // SHARDS

#: `--pr-start 6000` seeds the counter and the stub bumps it *before* each use,
#: so the first pull request it issues is 6001. The numbers matter less than
#: their being contiguous: a lost update on the counter under eight concurrent
#: claimers shows up here as a duplicate or a gap.
FIRST_PR = 6001

#: The agent writes one of these per pull request; check 9 reads the tickets
#: back out of them.
CLOSES = re.compile(r"Closes #(\d+)")


def api_base() -> str:
    return os.environ.get("GITHUB_API_URL") or "http://127.0.0.1:8765"


def bot_login() -> str:
    return os.environ.get("GITHUB_BOT_LOGIN") or "tina-demo-bot"


def get_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
        return json.loads(response.read())


def search(query: str) -> int:
    url = f"{api_base()}/search/issues?" + urllib.parse.urlencode({"q": query})
    return int(get_json(url)["total_count"])


def status_of(url: str) -> int:
    """The response code, including the ones urllib raises on."""
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def check(ok: bool, failure: str) -> None:
    """Assert, and take the recording down with it if it does not hold."""
    if not ok:
        print(f"audit.py: FAILED — {failure}", file=sys.stderr)
        raise SystemExit(1)


def load(paths: list[Path]) -> list[dict[str, Any]]:
    """Every JSON record the eight dispatchers wrote.

    A line that does not parse is a failure rather than a skip: this file exists
    to count things, and a counter that silently ignores what it cannot read is
    not counting.
    """
    records: list[dict[str, Any]] = []
    for path in paths:
        check(path.exists(), f"{path} does not exist — did the fan-out run?")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(
                    f"audit.py: FAILED — {path.name}:{number} is not JSON: {exc}"
                ) from exc
            check(isinstance(record, dict), f"{path.name}:{number} is not a JSON object")
            records.append(record)
    return records


def messages(records: list[dict[str, Any]], message: str) -> list[dict[str, Any]]:
    return [record for record in records if record.get("message") == message]


def audit_log(records: list[dict[str, Any]]) -> tuple[set[int], set[int]]:
    """Checks 1-6, over tina's own log stream. Returns (resolved, escalated)."""
    complete = messages(records, "run complete")

    # 1. One run record per ticket, and no ticket twice.
    check(
        len(complete) == TOTAL,
        f"expected {TOTAL} 'run complete' records, found {len(complete)}",
    )
    items = [str(record.get("item", "")) for record in complete]
    check(
        len(set(items)) == TOTAL,
        f"expected {TOTAL} distinct items, found {len(set(items))} —"
        f" {len(items) - len(set(items))} ticket(s) ran more than once",
    )

    # 2. The outcome mix, exactly. `agent.py` keys it off the ticket number, so
    #    it is a property of the run and not of how long anything took.
    tally = Counter(str(record.get("report", {}).get("outcome")) for record in complete)
    expected = {"resolved": RESOLVED, "needs_human": ESCALATED}
    check(
        dict(tally) == expected,
        f"outcome tally is {dict(tally)}, expected {expected}",
    )

    # 3. What each outcome is allowed to carry. `tina.verify` sets `verified`
    #    only for `resolved` runs with artifacts, so requiring `true` across the
    #    board would be requiring a bug.
    resolved: set[int] = set()
    escalated: set[int] = set()
    for record in complete:
        item = int(str(record["item"]))
        report = record.get("report", {})
        artifacts = report.get("artifacts") or []
        if report.get("outcome") == "resolved":
            resolved.add(item)
            check(
                report.get("verified") is True,
                f"#{item} resolved with verified={report.get('verified')!r}, expected true",
            )
            check(
                len(artifacts) == 1 and artifacts[0].get("kind") == "github:pr",
                f"#{item} resolved with artifacts {artifacts!r}, expected exactly one github:pr",
            )
        else:
            escalated.add(item)
            check(not artifacts, f"#{item} escalated but named artifacts {artifacts!r}")
            check(
                report.get("verified") is None,
                f"#{item} escalated with verified={report.get('verified')!r}, expected null",
            )

    # 4. Every worker the executor started also exited, and exited clean. A
    #    worker killed by a refused connection would otherwise be invisible:
    #    LocalExecutor logs the code and the dispatcher carries on.
    finished = messages(records, "worker finished")
    check(
        len(finished) == TOTAL,
        f"expected {TOTAL} 'worker finished' records, found {len(finished)}",
    )
    bad = [record for record in finished if record.get("exit_code") != 0]
    check(not bad, f"{len(bad)} worker(s) exited non-zero, first: {bad[0] if bad else ''}")

    # 5. Eight dispatchers, eight distinct tracks, 125 matched each. This is the
    #    disjointness: eight shards on one shared query would each match 1000.
    dispatching = messages(records, "dispatching")
    check(
        len(dispatching) == SHARDS,
        f"expected {SHARDS} 'dispatching' records, found {len(dispatching)}",
    )
    tracks = {str(record.get("track")) for record in dispatching}
    check(len(tracks) == SHARDS, f"expected {SHARDS} distinct tracks, found {sorted(tracks)}")
    for record in dispatching:
        check(
            record.get("matched") == PER_SHARD,
            f"track {record.get('track')} matched {record.get('matched')}, expected {PER_SHARD}",
        )

    # 6. These were real runs, not previews.
    previews = [record for record in records if "dry_run" in record]
    check(not previews, f"{len(previews)} record(s) carry a dry_run marker")

    return resolved, escalated


def audit_tracker(resolved: set[int], escalated: set[int]) -> tuple[list[int], int]:
    """Checks 7-11, over the tracker. Returns (pull request numbers, human-held)."""
    # 7. The ledger: nothing left unclaimed, everything claimed by the bot, and
    #    the twelve a human holds still held by them.
    unclaimed = search(f"repo:{REPO} is:issue is:open no:assignee label:bug")
    claimed = search(f"repo:{REPO} is:issue is:open assignee:{bot_login()} label:bug")
    held = search(f"repo:{REPO} is:issue is:open assignee:{HUMAN_LOGIN} label:bug")
    check(unclaimed == 0, f"{unclaimed} bug(s) still unassigned, expected 0")
    check(claimed == TOTAL, f"the bot holds {claimed} bugs, expected {TOTAL}")
    check(held == HUMAN_HELD, f"{HUMAN_LOGIN} holds {held} bugs, expected {HUMAN_HELD}")

    # 8. The pull requests, and the stub's counter under eight concurrent
    #    claimers: contiguous numbers are the serialization proof, since a lost
    #    update shows up as a duplicate or a gap.
    pulls = get_json(f"{api_base()}/repos/{REPO}/pulls")
    check(len(pulls) == RESOLVED, f"tracker holds {len(pulls)} pull requests, expected {RESOLVED}")
    numbers = [int(pull["number"]) for pull in pulls]
    check(
        sorted(numbers) == list(range(FIRST_PR, FIRST_PR + RESOLVED)),
        f"pull request numbers are not {RESOLVED} contiguous values from {FIRST_PR}:"
        f" {len(set(numbers))} distinct, {min(numbers)}-{max(numbers)}",
    )
    authors = {str(pull.get("user", {}).get("login")) for pull in pulls}
    check(authors == {bot_login()}, f"pull request authors are {sorted(authors)}")

    # 9. One pull request per resolved ticket: no ticket with two, no pull
    #    request for a ticket nobody worked.
    closes: list[int] = []
    for pull in pulls:
        found = CLOSES.findall(str(pull.get("body", "")))
        check(len(found) == 1, f"PR #{pull['number']} names {len(found)} tickets, expected 1")
        closes.append(int(found[0]))
    check(
        len(set(closes)) == RESOLVED,
        f"{len(set(closes))} distinct 'Closes #N' values, expected {RESOLVED}",
    )
    outside = sorted(number for number in closes if not FIRST_ITEM <= number <= LAST_ITEM)
    check(
        not outside, f"pull requests reference tickets outside {FIRST_ITEM}-{LAST_ITEM}: {outside}"
    )
    check(
        set(closes) == resolved,
        "the set of tickets with a pull request is not the set that resolved:"
        f" {len(set(closes) - resolved)} extra, {len(resolved - set(closes))} missing",
    )

    # 10. Verification is a real check, not a rubber stamp: a number the stub
    #     never issued is a 404, so `verified: true` cannot be earned by naming
    #     a plausible URL.
    invented = status_of(f"{api_base()}/{REPO}/pull/9999")
    check(invented == 404, f"an invented pull request URL returned {invented}, expected 404")

    # 11. The escalations are escalations: the factory produced nothing for them
    #     rather than producing something and hedging about it.
    bodies = "\n".join(str(pull.get("body", "")) for pull in pulls)
    leaked = sorted(number for number in escalated if f"#{number}" in bodies)
    check(not leaked, f"escalated tickets appear in pull request bodies: {leaked}")

    return numbers, held


def summary(numbers: list[int], held: int) -> list[str]:
    """Beat 5's closing figures -- every one of them just asserted above."""
    rows = [
        ("claimed", TOTAL, f" / {TOTAL}", f"unclaimed 0 · {held} held by a human, untouched"),
        ("run records", TOTAL, "", f"{TOTAL} distinct items · 0 double-runs"),
        (
            "pull requests",
            RESOLVED,
            "",
            f'{RESOLVED} distinct "Closes #N" · numbers {min(numbers)}-{max(numbers)}',
        ),
        ("escalated", ESCALATED, "", "needs_human · no PR, no guess"),
        ("verified", RESOLVED, f" / {RESOLVED}", "every artifact fetched back off the tracker"),
    ]
    return [f"{'  ' + label:<18}{value:>4}{ratio:<11}{note}" for label, value, ratio, note in rows]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="The volume demo's exactly-once audit.")
    parser.add_argument("logs", nargs="+", type=Path, help="The dispatchers' JSON streams.")
    args = parser.parse_args(argv[1:])

    resolved, escalated = audit_log(load(args.logs))
    numbers, held = audit_tracker(resolved, escalated)
    print("\n".join(summary(numbers, held)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
