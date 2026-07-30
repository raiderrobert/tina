#!/bin/sh
# The tracker views the recording opens and closes with -- plain curl and jq
# against the stub server, with no tina involved. Beat 1 of the demo is
# deliberately tina-free: it is the queue a person would look at before
# reaching for anything.
#
#     ./queue.sh bugs    open, unassigned bugs -- the same query the bug track runs
#     ./queue.sh prs     the pull requests the stub actually has on file
#
# Both take an optional `--summary`, which the volume demo uses: at 1,000 rows
# nobody scrolls the list, so the totals become the view.
#
#     ./queue.sh bugs --summary    the totals, the eight component queues, three rows
#     ./queue.sh prs --summary     the count, then the newest few
#
# Reads GITHUB_API_URL, which record.sh points at the stub.
set -eu

: "${GITHUB_API_URL:=http://127.0.0.1:8765}"

REPO=acme/api
# The `bug` track's query, verbatim from demo/tina.toml. Beat 1 shows the same
# rows dispatch is about to claim, not a different list that happens to agree.
QUERY="repo:$REPO is:issue is:open no:assignee label:bug"
# The same query with the unclaimed clause dropped, so the difference between
# the two totals is exactly the bugs somebody already holds.
QUERY_ALL="repo:$REPO is:issue is:open label:bug"
# The eight component queues the volume demo's eight tracks follow, in the order
# the stub hands them out.
COMPONENTS='api web auth data infra jobs sdk cli'

search() {
    curl --silent --show-error --get \
        --data-urlencode "q=$1" \
        "$GITHUB_API_URL/search/issues"
}

bugs() {
    printf '%s  open bugs, no assignee\n' "$REPO"
    search "$QUERY" |
        jq -r '
            (.items[] | "  #\(.number)  \(.title)"),
            "  \(.total_count) waiting"'
}

# Nine numbers out of two real responses: the two totals, and the per-component
# split grouped client-side from the unassigned payload. Nothing here reports a
# number it did not fetch.
bugs_summary() {
    unassigned=$(search "$QUERY")
    all=$(search "$QUERY_ALL" | jq -r '.total_count')

    printf '%s' "$unassigned" | jq -r --argjson all "$all" '
        "  \($all) open bugs · label:bug — \(.total_count) unassigned,"
        + " \($all - .total_count) already held by a human"'

    counts=$(
        printf '%s' "$unassigned" | jq -r --arg components "$COMPONENTS" '
            def tally($c): [.items[] | select([.labels[].name] | index($c))] | length;
            [tally($components | split(" ")[])] | join(" ")'
    )
    # Deliberate word splitting: eight counts, in COMPONENTS order.
    # shellcheck disable=SC2086
    set -- $counts
    printf '  %5s %3s   %5s %3s   %5s %3s   %5s %3s\n' api "$1" web "$2" auth "$3" data "$4"
    printf '  %5s %3s   %5s %3s   %5s %3s   %5s %3s\n' infra "$5" jobs "$6" sdk "$7" cli "$8"

    printf '%s' "$unassigned" | jq -r '
        (.items[:3][] | "  #\(.number)  \((.title + (" " * 60))[0:56])  "
            + (([.labels[].name] - ["bug"]) | first // "")),
        "  … \(.total_count - 3) more"'
}

pulls() {
    curl --silent --show-error "$GITHUB_API_URL/repos/$REPO/pulls"
}

prs() {
    printf '%s  pull requests\n' "$REPO"
    pulls |
        jq -r '
            (.[] | "  #\(.number)  \((.title + (" " * 60))[0:60])  \(.user.login)"),
            "  \(length) open, awaiting review"'
}

# The review queue at volume: the count is the story, and the newest few rows are
# there to show they are real pull requests with a real author.
prs_summary() {
    pulls |
        jq -r '
            "  \(length) pull requests open · awaiting review",
            (.[-3:][] | "  #\(.number)  \((.title + (" " * 60))[0:56])  \(.user.login)")'
}

case "${1:-}${2:+ $2}" in
bugs) bugs ;;
prs) prs ;;
"bugs --summary") bugs_summary ;;
"prs --summary") prs_summary ;;
*)
    echo "usage: queue.sh bugs|prs [--summary]" >&2
    exit 2
    ;;
esac
