#!/bin/sh
# Build the recording's working directory, and assert it is still the published
# example:
#
#     ./demo/workdir.sh <dir>     build the working directory in <dir>
#     ./demo/workdir.sh --check   build into a temp dir, assert, remove it, print ok
#
# There is exactly one copy of the track in this repo -- examples/bug-triage/ --
# and this is the one place the recording's config is derived from it. The
# derivation is one `sed` substitution (`harness = "pi"` -> `harness = "demo"`)
# plus demo/overlay.toml's `[harnesses.demo]` table, and the round trip back is
# asserted byte for byte. If the two ever diverge by anything else, this exits
# non-zero and no gif gets made.
#
# `--check` needs only sh, sed, grep, diff and cp -- no asciinema, no agg -- so
# `just check` runs it on every PR.
set -eu

DEMO_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH='' cd -- "$DEMO_DIR/.." && pwd)
EXAMPLE_DIR="$ROOT/examples/bug-triage"
EXAMPLE_CONFIG="$EXAMPLE_DIR/tina.toml"
EXAMPLE_SKILL="$EXAMPLE_DIR/tracks/triage/SKILL.md"
OVERLAY="$DEMO_DIR/overlay.toml"

die() {
    echo "workdir.sh: $1" >&2
    exit 1
}

CHECK_DIR=''
ROUNDTRIP=''
cleanup() {
    if [ -n "$ROUNDTRIP" ]; then
        rm -f "$ROUNDTRIP"
    fi
    if [ -n "$CHECK_DIR" ]; then
        rm -rf "$CHECK_DIR"
    fi
}
trap cleanup EXIT INT TERM

case "${1:-}" in
--check)
    CHECK_DIR=$(mktemp -d)
    DIR="$CHECK_DIR"
    ;;
'' | -*)
    echo "usage: workdir.sh <dir> | workdir.sh --check" >&2
    exit 2
    ;;
*)
    DIR="$1"
    ;;
esac

# Value of a `key = "value"` line, which must appear exactly once with no inline
# comment. demo/workdir.sh and demo/queue.sh read the track out of the config
# this way, so that shape is a parsing contract (see examples/bug-triage/).
toml_value() {
    _key="$1"
    _file="$2"
    _n=$(grep -c "^$_key = \"" "$_file" || true)
    if [ "$_n" -ne 1 ]; then
        die "expected exactly one '$_key = \"...\"' line in $_file, found $_n"
    fi
    sed -n "s/^$_key = \"\\(.*\\)\"\$/\\1/p" "$_file"
}

# 1. There is no second copy of the track.
if [ -e "$DEMO_DIR/tina.toml" ] || [ -e "$DEMO_DIR/tracks" ]; then
    die "the track lives in examples/bug-triage/; a copy under demo/ means the gif has stopped being evidence for the example"
fi

# 2. The example is present and shaped for the substitution.
[ -f "$EXAMPLE_CONFIG" ] || die "missing $EXAMPLE_CONFIG -- the recording has no track to run"
[ -f "$EXAMPLE_SKILL" ] || die "missing $EXAMPLE_SKILL -- the recording has no skill to put in the prompt"
pi_lines=$(grep -cx 'harness = "pi"' "$EXAMPLE_CONFIG" || true)
if [ "$pi_lines" -ne 1 ]; then
    die "expected exactly one 'harness = \"pi\"' line in $EXAMPLE_CONFIG, found $pi_lines -- the overlay has no substitution target"
fi

# 3. The overlay supplies a harness and nothing else. Appending is only valid
#    TOML because it opens with a table header; a bare key would land inside the
#    example's last table. This guard is what stops a `[bug]` table quietly
#    coming back under demo/.
[ -f "$OVERLAY" ] || die "missing $OVERLAY -- the recording has no harness"
headers=$(grep -c '^\[' "$OVERLAY" || true)
harness_headers=$(grep -c '^\[harnesses\.' "$OVERLAY" || true)
if [ "$headers" -ne "$harness_headers" ] || [ "$headers" -eq 0 ]; then
    die "$OVERLAY may only declare [harnesses.*] tables -- everything else about the track comes from the example"
fi
first_line=$(sed -n '/^[[:space:]]*$/d; /^[[:space:]]*#/d; p' "$OVERLAY" | head -n 1)
case "$first_line" in
"[harnesses."*) ;;
*) die "$OVERLAY must open with a [harnesses.*] header, not '$first_line' -- it is appended to the example's last table" ;;
esac

# 4. Build.
mkdir -p "$DIR"
sed 's/^harness = "pi"$/harness = "demo"/' "$EXAMPLE_CONFIG" >"$DIR/tina.toml"
cat "$OVERLAY" >>"$DIR/tina.toml"

tracks_dir=$(toml_value tracks_dir "$DIR/tina.toml")
[ -d "$EXAMPLE_DIR/$tracks_dir" ] || die "the example's tracks_dir '$tracks_dir' is not a directory under $EXAMPLE_DIR"
rm -rf "$DIR/$tracks_dir"
cp -R "$EXAMPLE_DIR/$tracks_dir" "$DIR/$tracks_dir"
cp "$DEMO_DIR/queue.sh" "$DIR/queue.sh"

# 5. The round trip. Reverse the substitution over the example's own line count
#    and require the result to be the published file, byte for byte. This is the
#    drift gate, and it is the point of the whole arrangement.
example_lines=$(wc -l <"$EXAMPLE_CONFIG" | tr -d ' ')
ROUNDTRIP=$(mktemp)
head -n "$example_lines" "$DIR/tina.toml" |
    sed 's/^harness = "demo"$/harness = "pi"/' >"$ROUNDTRIP"
if ! diff -u "$EXAMPLE_CONFIG" "$ROUNDTRIP" >&2; then
    die "the recording's config is no longer the published example -- the gif would stop being evidence for examples/bug-triage/"
fi
# ...and everything past the example's own lines is the overlay and nothing
# else, which is the other half of "one line changed, one harness table added".
tail -n "+$((example_lines + 1))" "$DIR/tina.toml" >"$ROUNDTRIP"
if ! diff -u "$OVERLAY" "$ROUNDTRIP" >&2; then
    die "the recording's config carries something past the example that is not demo/overlay.toml"
fi

# 6. The stub serves exactly one repo, so an example naming another one would
#    record a screenful of 404s.
config_repo=$(toml_value repo "$DIR/tina.toml")
stub_repo=$(sed -n 's/^REPO = "\(.*\)"$/\1/p' "$DEMO_DIR/stub_server.py")
agent_repo=$(sed -n 's/^REPO = "\(.*\)"$/\1/p' "$DEMO_DIR/agent.py")
if [ "$config_repo" != "$stub_repo" ] || [ "$config_repo" != "$agent_repo" ]; then
    die "repo mismatch: config '$config_repo', stub_server.py '$stub_repo', agent.py '$agent_repo' -- the recording would be all 404s"
fi

# 7. Hand the derived track to queue.sh, so the query is not copied a third
#    time. record.sh sources this file, hence the quoting -- the query has
#    spaces in it, and a value carrying a quote of its own would not survive.
config_query=$(toml_value query "$DIR/tina.toml")
case "$config_repo$config_query" in
*\'*) die "the example's repo or query contains a single quote, which .demo-env cannot carry to queue.sh" ;;
esac
{
    echo "DEMO_REPO='$config_repo'"
    echo "DEMO_QUERY='$config_query'"
} >"$DIR/.demo-env"

if [ -n "$CHECK_DIR" ]; then
    echo "workdir.sh: ok -- the recording's config is examples/bug-triage/tina.toml with harness = \"demo\" and [harnesses.demo]"
fi
