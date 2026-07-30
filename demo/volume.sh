#!/bin/sh
# The volume session that gets recorded. Run it through `demo/record.sh volume`,
# which starts the stub tracker with 1,012 issues and exports the environment
# this expects.
#
# Six beats: the backlog, the fan-out, live progress, the end state, the two
# honesty checks, the closer. Six and not five because the exactly-once claim
# has to be on screen and gating -- `audit.py` in beat 5 exits non-zero on any
# violation, this script runs under `set -eu`, and `asciinema rec --return`
# propagates that, so a failed assertion kills the recording before `agg` runs.
#
# asciinema records a scripted command with nobody at the keyboard, so there is
# no typing to capture: each command is echoed as a prompt line before it runs,
# which is what makes the gif read as a session rather than as raw output.
set -eu

: "${TINA:=tina}"
# Set by record.sh, which passes the same value to `agg --speed`, so beat 6's
# caption cannot disagree with how fast the gif actually plays.
: "${DEMO_SPEED:=1}"

# The eight component queues, one track each. Eight disjoint queries rather than
# eight dispatchers on one shared query: on one query all eight would take the
# same `items[:125]` and work every ticket eight times, and with a single bot
# login `GitHubSource.claim` cannot reject the duplicates either. See
# demo/tina-volume.toml.
COMPONENTS='api web auth data infra jobs sdk cli'
LIMIT=125

prompt() {
    printf '\033[1;32m$\033[0m %s\n' "$1"
    sleep 0.7
}

# The continuation prompt, for the one command too long to sit on a 98-column
# line. Same convention an interactive shell uses, so the fan-out reads as the
# one command it is rather than as three.
cont() {
    printf '\033[1;32m>\033[0m %s\n' "$1"
}

pause() {
    printf '\n'
    sleep 1.4
}

# Beat 6 is a caption, not a command. It gets no `$` and never goes through
# `prompt`, so nothing in it can be misread as something the recording ran.
caption() {
    printf '\033[0;90m%s\033[0m\n' "$1"
}

# Wipe the screen at a beat boundary. This is the recording's largest size
# lever, and it is not decoration: beats 1-3 leave the 30-row terminal full, so
# every line printed after that scrolls all thirty and `agg` has to store a
# whole-screen frame. Measured on this session -- beats 4-6 rendered as nine
# 831x563 repaints costing 22-48 KB each, ~330 KB of a 474 KB gif. Printed
# rather than `clear`, which needs a terminfo entry the recording should not
# depend on. It is a beat boundary and never a repaint loop: `progress.py`
# rewrites its block in place for exactly the same reason.
screen() {
    printf '\033[2J\033[H'
}

# Beat 5's first half. `already claimed` and `run complete` are the two lines
# that matter out of the run's JSON stream; the rest is httpx and the executor.
# The artifact count is printed rather than assumed: a yielding worker opening a
# pull request anyway is exactly the thing worth being able to see.
JQ_RUN='
    select(.message == "already claimed" or .message == "run complete")
    | if .report then "  #\(.item)  \(.report.outcome) · \(.report.details)"
        + " · \(.report.artifacts|length) pull requests" else "  #\(.item)  \(.message)" end'

# Beat 1 — the backlog. No tina on screen: this is the tracker view a person
# would be looking at. Nobody scrolls a thousand rows, so the totals are the
# view -- and the eight component counts are the shape the fan-out will follow.
prompt './queue.sh bugs --summary'
./queue.sh bugs --summary
pause

# Beat 2 — the fan-out. Eight real `tina dispatch` processes, side by side. Each
# stream goes to its own file rather than a shared pipe: a `run complete` line is
# ~430 bytes and PIPE_BUF is 512 here, so eight writers into one pipe are one
# agent-authored `details` string away from interleaving mid-line.
#
# This is the local stand-in for the production shape. In production one
# dispatcher fans out concurrent Cloud Run job executions (ADR-003); the local
# executor runs its workers as blocking subprocesses one after another, so local
# concurrency has to come from running eight dispatchers. Beat 6 says so.
prompt 'for c in api web auth data infra jobs sdk cli; do'
cont '    tina dispatch --track bug-$c --limit 125 >runs-$c.jsonl 2>&1 &'
cont 'done'

started=$(date +%s)
dispatchers=''
for c in $COMPONENTS; do
    $TINA dispatch --track "bug-$c" --limit "$LIMIT" >"runs-$c.jsonl" 2>&1 &
    dispatchers="$dispatchers $!"
    # The shell reporting its own jobs: a real pid and a real track name.
    printf '  dispatcher %-7s track %-9s limit %s\n' "$!" "bug-$c" "$LIMIT"
done
pause

# Beat 3 — live progress, rewritten in place. Polled off the dispatchers' files
# and the stub's own endpoints; it exits when every ticket has been claimed and
# every run has reported, and non-zero if that never happens.
prompt './progress.py runs-*.jsonl'
./progress.py runs-*.jsonl

# The last `run complete` is written by a worker; its dispatcher still has a
# `worker finished` line to flush after that. Waiting here is what makes beat 5's
# audit see a complete stream, and it is also what makes `started`/`finished`
# bracket the whole fan-out rather than most of it.
for pid in $dispatchers; do
    wait "$pid"
done
finished=$(date +%s)
pause

# Beat 4 — the end state. `tina status` is the product's own command answering
# the "queue empty, tickets claimed" half off two live tracker queries. One
# shard, not eight: eight of these is 32 lines of near-identical output, and the
# whole-backlog counts are asserted for all 1,000 in beat 5. Its stdout is the
# JSON stream and the counts are the stderr half of tina's output boundary, so
# the stream is redirected to keep them readable at 98 columns.
#
# The remaining three beats are sized to land inside one screen, so nothing
# after this scrolls.
screen
prompt 'tina status --track bug-api >/dev/null'
$TINA status --track bug-api >/dev/null
pause

prompt './queue.sh prs --summary'
./queue.sh prs --summary
pause

# Beat 5 — the two honesty checks.
#
# #5001 is one of the twelve a human already holds. This is the real ADR-004
# loser path and the only place it genuinely fires: the claim is rejected because
# a *different* login holds the item, which is deterministic because the holder
# was seeded at stub startup rather than produced by a race.
prompt "tina run --track bug-api --item 5001 | jq -r '$JQ_RUN'"
$TINA run --track bug-api --item 5001 | jq -r --unbuffered "$JQ_RUN"
pause

prompt './audit.py runs-*.jsonl'
./audit.py runs-*.jsonl
pause

# Beat 6 — the closer. Not run. The three counts are the ones `audit.py` just
# asserted one beat above, so the recording cannot reach here carrying different
# ones. The elapsed seconds are measured either side of the fan-out and the
# speed is record.sh's, which is the same value it hands `agg`; neither is a
# literal in this file.
caption '  1000 tickets · 967 pull requests open for review · 33 escalated to a human'
caption "  real time $((finished - started))s — this recording is sped up ${DEMO_SPEED}x"
caption '  fan-out runs on Cloud Run in production: executor = "cloudrun"'
caption '  github.com/raiderrobert/tina'
sleep 2.5
