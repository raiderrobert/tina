#!/bin/sh
# The tracker views the recording opens and closes with -- plain curl and jq
# against the stub server, with no tina involved. Beat 1 of the demo is
# deliberately tina-free: it is the queue a person would look at before
# reaching for anything.
#
#     ./queue.sh bugs    open, unassigned bugs -- the same query the bug track runs
#     ./queue.sh prs     the pull requests the stub actually has on file
#
# Reads GITHUB_API_URL, which record.sh points at the stub, and DEMO_REPO and
# DEMO_QUERY, which record.sh derives from examples/bug-triage/tina.toml.
set -eu

: "${GITHUB_API_URL:=http://127.0.0.1:8765}"

# The repo and the query come from examples/bug-triage/tina.toml, read out of
# the derived config by workdir.sh and exported by record.sh. There is no
# literal fallback here on purpose: a fallback is a second copy of the track
# with extra steps, and beat 1 would be free to show rows dispatch is not about
# to claim.
: "${DEMO_REPO:?queue.sh gets its repo and query from the example config; run it through record.sh}"
: "${DEMO_QUERY:?queue.sh gets its repo and query from the example config; run it through record.sh}"

REPO="$DEMO_REPO"
QUERY="$DEMO_QUERY"

bugs() {
    printf '%s  open bugs, no assignee\n' "$REPO"
    curl --silent --show-error --get \
        --data-urlencode "q=$QUERY" \
        "$GITHUB_API_URL/search/issues" |
        jq -r '
            (.items[] | "  #\(.number)  \(.title)"),
            "  \(.total_count) waiting"'
}

prs() {
    printf '%s  pull requests\n' "$REPO"
    curl --silent --show-error "$GITHUB_API_URL/repos/$REPO/pulls" |
        jq -r '
            (.[] | "  #\(.number)  \((.title + (" " * 60))[0:60])  \(.user.login)"),
            "  \(length) open, awaiting review"'
}

case "${1:-}" in
bugs) bugs ;;
prs) prs ;;
*)
    echo "usage: queue.sh bugs|prs" >&2
    exit 2
    ;;
esac
