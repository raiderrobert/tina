"""The demo's stand-in for an agent harness.

Tina never parses harness stdout: the agent writes `outcome.json` to the
directory Tina hands it, and that file is the whole contract. This script is
the smallest thing that honours it -- it reads the prompt, prints a couple of
lines so the recording shows the harness running, and writes a `resolved`
report naming a pull request the stub server actually serves.

Invoked as `python3 -m agent {prompt_file} {outcome_dir}` from
`[harnesses.demo]`; `record.sh` puts this directory on `PYTHONPATH`.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PULL_REQUEST = 4851


def report(api_base: str) -> dict[str, object]:
    return {
        "outcome": "resolved",
        "details": (
            "Reproduced the crash, added a guard for the empty payload, "
            "and opened a pull request with a regression test."
        ),
        "artifacts": [{"kind": "github:pr", "url": f"{api_base}/acme/api/pull/{PULL_REQUEST}"}],
    }


def main(argv: list[str]) -> int:
    prompt_file, outcome_dir = Path(argv[1]), Path(argv[2])
    prompt = prompt_file.read_text(encoding="utf-8")
    if "Triage" not in prompt:
        print("the prompt is missing the activity skill", file=sys.stderr)
        return 1

    api_base = os.environ.get("GITHUB_API_URL", "http://127.0.0.1:8765")
    payload = json.dumps(report(api_base), indent=2)
    (outcome_dir / "outcome.json").write_text(payload, encoding="utf-8")

    # A copy outside Tina's temporary workdir, so the recording can `cat` the
    # same bytes after the run has cleaned up after itself.
    copy = os.environ.get("TINA_DEMO_OUTCOME")
    if copy:
        Path(copy).write_text(payload, encoding="utf-8")

    print(f"agent: read {len(prompt)} chars of prompt")
    print(f"agent: opened {api_base}/acme/api/pull/{PULL_REQUEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
