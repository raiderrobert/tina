#!/bin/sh
# The tracker views the recording opens and closes with -- plain curl and jq
# against the stub server, with no tina involved. Beat 1 of the demo is
# deliberately tina-free: it is the queue a person would look at before
# reaching for anything.
#
#     ./queue.sh bugs    open, unassigned bugs -- the same query the bug track runs
#     ./queue.sh prs     the pull requests the stub actually has on file
#
# Reads GITHUB_API_URL, which record.sh points at the stub.
set -eu

: "${GITHUB_API_URL:=http://127.0.0.1:8765}"

REPO=acme/api
# The `bug` track's query, verbatim from demo/tina.toml. Beat 1 shows the same
# rows dispatch is about to claim, not a different list that happens to agree.
QUERY="repo:$REPO is:issue is:open no:assignee label:bug"

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
