"""The demo's stand-in for an agent harness.

Tina never parses harness stdout: the agent writes `outcome.json` to the
directory Tina hands it, and that file is the whole contract. This script is the
smallest thing that honours it -- it reads the work item out of the prompt,
opens one pull request per ticket against the stub server, and reports that
pull request as its artifact.

Two rules it lives by:

- It writes **nothing** to stdout. Stdout belongs to `tina.log`'s JSON stream,
  which the recording pipes through jq; one non-JSON line there aborts jq.
  Diagnostics go to stderr.
- It reports the `html_url` the stub handed back, never a URL it made up. The
  stub assigns pull request numbers itself and 404s every number it did not
  issue, so a report naming an invented URL would come back `verified: false`.

Invoked as `python3 -m agent {prompt_file} {outcome_dir}` from
`[harnesses.demo]`; `record.sh` puts this directory on `PYTHONPATH`.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO = "acme/api"
TIMEOUT = 10.0

#: The fake harness's stand-in for doing the work. Its only job is to make ten
#: runs watchable rather than a sub-second flash -- this is the harness's own
#: runtime, not staged output.
WORK_SECONDS = 0.35

#: `tina.prompt.build` embeds the WorkItem as JSON in the prompt's one fenced
#: block, so the agent reads the ticket from there rather than hardcoding it.
WORK_ITEM = re.compile(r"```json\n(?P<json>.*?)\n```", re.DOTALL)


def work_item(prompt: str) -> dict[str, Any]:
    """The WorkItem Tina embedded in the prompt."""
    match = WORK_ITEM.search(prompt)
    if not match:
        raise ValueError("the prompt carries no work item JSON block")
    parsed = json.loads(match["json"])
    if not isinstance(parsed, dict) or "id" not in parsed:
        raise ValueError("the prompt's JSON block is not a work item")
    return parsed


def open_pull_request(api_base: str, item: dict[str, Any]) -> dict[str, Any]:
    """POST one pull request for this ticket and return what the server stored."""
    number = item["id"]
    payload = json.dumps(
        {
            "title": f"Fix #{number}: {item.get('title', '')}",
            "head": f"tina/bug-{number}",
            "base": "main",
            "body": f"Closes #{number}\n\nOpened by the tina demo harness.",
        }
    ).encode()
    request = urllib.request.Request(
        f"{api_base}/repos/{REPO}/pulls",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read())


def report(item: dict[str, Any], pull_request: dict[str, Any]) -> dict[str, object]:
    """A `resolved` report whose `details` claim only what this harness did."""
    return {
        "outcome": "resolved",
        "details": (
            f"Read the report for #{item['id']} ({item.get('title', '')}) and opened "
            f"pull request #{pull_request['number']} against main. This is the demo "
            f"harness: it opens the pull request and reports it, and writes no code."
        ),
        "artifacts": [{"kind": "github:pr", "url": pull_request["html_url"]}],
    }


def failure(reason: str) -> dict[str, object]:
    """A failed run names no artifact -- there is no pull request to name."""
    return {"outcome": "failed", "details": reason, "artifacts": []}


def main(argv: list[str]) -> int:
    prompt_file, outcome_dir = Path(argv[1]), Path(argv[2])
    prompt = prompt_file.read_text(encoding="utf-8")
    if "Triage" not in prompt:
        print("the prompt is missing the track skill", file=sys.stderr)
        return 1

    api_base = os.environ.get("GITHUB_API_URL", "http://127.0.0.1:8765")
    item = work_item(prompt)
    time.sleep(WORK_SECONDS)

    try:
        pull_request = open_pull_request(api_base, item)
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        # No pull request means no `resolved`: reporting one here would be the
        # exact lie tina's verification exists to catch.
        reason = f"could not open a pull request for #{item['id']}: {exc}"
        print(reason, file=sys.stderr)
        (outcome_dir / "outcome.json").write_text(
            json.dumps(failure(reason), indent=2), encoding="utf-8"
        )
        return 1

    (outcome_dir / "outcome.json").write_text(
        json.dumps(report(item, pull_request), indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
