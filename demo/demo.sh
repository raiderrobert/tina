#!/bin/sh
# The session that gets recorded. Run it through demo/record.sh, which starts
# the stub tracker and exports the environment this expects.
#
# Four beats: the queue, the one command, the review queue, the closer.
#
# asciinema records a scripted command with nobody at the keyboard, so there is
# no typing to capture: each command is echoed as a prompt line before it runs,
# which is what makes the gif read as a session rather than as raw output.
set -eu

: "${TINA:=tina}"

prompt() {
    printf '\033[1;32m$\033[0m %s\n' "$1"
    sleep 0.7
}

pause() {
    printf '\n'
    sleep 1.4
}

# Beat 4 is a caption, not a command. It gets no `$` and never goes through
# `prompt`, so nothing in it can be misread as something the recording ran.
caption() {
    printf '\033[0;90m%s\033[0m\n' "$1"
}

# One line per finished ticket, out of the JSON stream tina writes to stdout.
# `select` is what keeps this to exactly ten lines: the raw stream also carries
# httpx request lines, the executor's worker lines, and dispatch's own.
# `.report.artifacts[0].url` is deliberately strict -- a run that resolved
# without an artifact should break the recording, not print a blank cell.
JQ_FILTER='
    select(.message == "run complete")
    | "  #\(.item)  →  PR #\(.report.artifacts[0].url | split("/") | last)"
      + "  \(.report.outcome) · \(if .report.verified then "verified" else "UNVERIFIED" end)"'

# Beat 1 — the queue. No tina on screen: this is the tracker view a person
# would be looking at before reaching for anything.
prompt './queue.sh bugs'
./queue.sh bugs
pause

# Beat 2 — the one command. A real dispatch, ten real runs, piped live through
# jq. The pipe is the point: the log is structured, so it is filterable.
prompt "tina dispatch --track bug --limit 10 | jq -r --unbuffered '$JQ_FILTER'"
$TINA dispatch --track bug --limit 10 | jq -r --unbuffered "$JQ_FILTER"
pause

# Beat 3 — the review queue. `tina status` reports the counts off two live
# tracker queries; stdout is the JSON stream, and the counts are the stderr
# half of tina's output boundary, so the stream is redirected to keep them
# readable here. `queue.sh prs` is the other half: real server state.
prompt 'tina status --track bug >/dev/null'
$TINA status --track bug >/dev/null
pause

prompt './queue.sh prs'
./queue.sh prs
pause

# Beat 4 — the closer. Not run: after beat 2 every issue is claimed, so a
# `no:assignee` query would match nothing and the preview would be a dud.
caption '  Point it at your own tracker first — nothing is claimed, nothing is written:'
caption ''
caption '      tina dispatch --track bug --dry-run'
caption ''
caption '  https://github.com/raiderrobert/tina'
sleep 2.5
