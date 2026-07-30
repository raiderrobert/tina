#!/usr/bin/env python3
"""Live progress for the volume demo, read out of real state.

Beat 3 of `volume.sh`. Eight `tina dispatch` processes are draining eight
component queues into eight `runs-*.jsonl` files; this watches them finish.

Two sources, both real:

- **The dispatchers' own JSON log stream.** Each file is re-read forward from a
  saved byte offset, so the cost per poll is the new bytes and not the file. A
  dispatcher can be caught mid-write, so a trailing partial line is buffered
  rather than parsed; a parse error on a *complete* line is a hard failure, not
  a skipped record -- a demo that quietly dropped records would be counting
  something other than what happened.
- **The stub tracker.** `assignee:<bot> label:bug` is what is claimed and
  `GET /repos/{repo}/pulls` is what is open, which is the same pair of endpoints
  `queue.sh` and the audit read. Nothing here is advanced by a timer.

Six lines, rewritten in place with `\\033[6A` -- no `clear`, no `\\033[2J`. A
full repaint every frame is what makes a recording expensive to render: an
in-place counter changes a handful of character cells, so `agg`'s per-frame diff
stays small and the refresh rate is nearly free.

Nothing on screen is labelled `escalated` while the fan-out is running.
Mid-run, `claimed - PRs open` is "in flight *or* escalated" and the tracker
cannot tell those apart; the split is only knowable once nothing is in flight,
which is beat 5's job.

    ./progress.py runs-*.jsonl [--timeout 300] [--interval 0.5]

Exits 0 when every ticket is claimed and every run has reported. Exits non-zero
on the timeout, so a stalled fan-out fails the recording instead of hanging it.
stdlib only; reads `GITHUB_API_URL` and `GITHUB_BOT_LOGIN` from the environment.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

REPO = "acme/api"
TIMEOUT = 10.0

#: How many lines the block is. The cursor moves up exactly this far between
#: frames, so it and the renderer cannot drift apart.
LINES = 6

#: Completions shown under the counter. Three is the tail §10 costs out; it is
#: the first thing to trim if the gif ever goes over budget.
TAIL = 3

#: Width of the proportional bar, chosen to leave the whole block inside 98
#: columns with room for the percentage.
BAR_WIDTH = 60

#: The dispatch query without its component label: every workable bug, and the
#: same text `tina-volume.toml`'s eight tracks share.
UNCLAIMED_QUERY = f"repo:{REPO} is:issue is:open no:assignee label:bug"


def api_base() -> str:
    return os.environ.get("GITHUB_API_URL") or "http://127.0.0.1:8765"


def bot_login() -> str:
    return os.environ.get("GITHUB_BOT_LOGIN") or "tina-demo-bot"


def get_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
        return json.loads(response.read())


def search_count(query: str) -> int:
    """`total_count` for one tracker search."""
    url = f"{api_base()}/search/issues?" + urllib.parse.urlencode({"q": query})
    return int(get_json(url)["total_count"])


def open_pull_requests() -> int:
    """How many pull requests the tracker is holding."""
    return len(get_json(f"{api_base()}/repos/{REPO}/pulls"))


def claimed_count() -> int:
    """The bugs the bot holds -- the other half of the dispatch query."""
    return search_count(f"repo:{REPO} is:issue is:open assignee:{bot_login()} label:bug")


def backlog_total() -> int:
    """The denominator: every bug the eight tracks can work, claimed or not.

    Read, not hardcoded -- but read as the *sum* of the two halves rather than
    off the unclaimed query alone. This starts after beat 2 has already launched
    the fan-out, so by now the unclaimed count is short by however many tickets
    the dispatchers got to first. A claim only moves an issue from one half to
    the other, so the sum is the same number at any moment during the run. The
    twelve a human holds are in neither half, which is why they are not in the
    denominator: they are not the factory's to work.
    """
    return search_count(UNCLAIMED_QUERY) + claimed_count()


class Tail:
    """One dispatcher's JSON stream, read forward from a saved byte offset.

    Bytes rather than text: a chunk boundary can fall inside a multi-byte
    character as easily as inside a line, and both have to survive to the next
    poll intact.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = 0
        self.buffer = b""

    def records(self) -> list[dict[str, Any]]:
        """Whatever whole lines have appeared since the last call."""
        if not self.path.exists():
            return []
        with self.path.open("rb") as handle:
            handle.seek(self.offset)
            chunk = handle.read()
            self.offset = handle.tell()
        if not chunk:
            return []

        *complete, self.buffer = (self.buffer + chunk).split(b"\n")
        parsed: list[dict[str, Any]] = []
        for line in complete:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(
                    f"progress.py: {self.path.name}: unparsable log line: {exc}"
                ) from exc
            if isinstance(record, dict):
                parsed.append(record)
        return parsed


def completion_line(record: dict[str, Any]) -> str:
    """One finished ticket, rendered.

    A `needs_human` run produced no artifact, so it gets no arrow and no pull
    request number rather than a blank cell -- which is also why this beat is
    python and not the strict `.report.artifacts[0].url` jq filter the simple
    demo uses: that filter would abort here.
    """
    report = record.get("report") or {}
    outcome = str(report.get("outcome", "?"))
    artifacts = report.get("artifacts") or []
    if not artifacts:
        return f"  #{record.get('item', '?')}  {outcome}"
    number = str(artifacts[0].get("url", "")).rstrip("/").rsplit("/", 1)[-1]
    verdict = "verified" if report.get("verified") else "UNVERIFIED"
    return f"  #{record.get('item', '?')}  →  PR #{number}  {outcome} · {verdict}"


def bar(done: int, total: int) -> str:
    filled = round(BAR_WIDTH * done / total) if total else 0
    percent = round(100 * done / total) if total else 0
    return f"  [{'█' * filled}{'░' * (BAR_WIDTH - filled)}]  {percent:>3}%"


def block(claimed: int, prs: int, total: int, elapsed: int, tail: list[str]) -> list[str]:
    """The six lines, always six: the renderer moves the cursor by a constant."""
    lines = [
        f"  claimed {claimed:>5}/{total} · PRs open {prs:>4}"
        f" · unclaimed {total - claimed:>4} · elapsed {elapsed}s",
        bar(claimed, total),
        *tail[-TAIL:],
    ]
    lines.extend("" for _ in range(LINES - 1 - len(lines)))
    lines.append("")
    return lines


def render(lines: list[str], first: bool) -> None:
    """Repaint the block where it already is."""
    out = [] if first else [f"\033[{LINES}A"]
    out.extend(f"\033[K{line}\n" for line in lines)
    sys.stdout.write("".join(out))
    sys.stdout.flush()


def watch(paths: list[Path], interval: float, timeout: float) -> int:
    total = backlog_total()
    tails = [Tail(path) for path in paths]
    completed: list[str] = []
    done = 0
    started = time.monotonic()
    first = True

    while True:
        for tail in tails:
            for record in tail.records():
                if record.get("message") == "run complete":
                    done += 1
                    completed.append(completion_line(record))

        claimed = claimed_count()
        elapsed = time.monotonic() - started
        render(block(claimed, open_pull_requests(), total, round(elapsed), completed), first)
        first = False

        if claimed >= total and done >= total:
            return 0
        if elapsed > timeout:
            print(
                f"progress.py: gave up after {timeout:g}s with {done}/{total} runs reported",
                file=sys.stderr,
            )
            return 1
        time.sleep(interval)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Live progress for the volume demo.")
    parser.add_argument("logs", nargs="+", type=Path, help="The dispatchers' JSON streams.")
    parser.add_argument("--interval", type=float, default=0.5, help="Seconds between polls.")
    parser.add_argument("--timeout", type=float, default=300.0, help="Give up after this long.")
    args = parser.parse_args(argv[1:])
    return watch(args.logs, args.interval, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
