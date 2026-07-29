#!/bin/sh
# Regenerate tina-demo.gif. One command, no arguments, no network:
#
#     ./demo/record.sh
#
# Starts the stub tracker on a free port, records demo.sh with asciinema,
# renders the cast with agg, and tears the stub down again -- including when
# something fails partway. Requires asciinema, agg, python3, and either `tina`
# on PATH or `uv` to run it out of this checkout.
set -eu

DEMO_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH='' cd -- "$DEMO_DIR/.." && pwd)
GIF="$ROOT/tina-demo.gif"
BUDGET=600000

# Terminal geometry and timing are pinned here, so two recordings on the same
# machine differ only in timing jitter.
COLS=98
ROWS=30
FONT_SIZE=14

WORK=$(mktemp -d)
STUB_PID=''

cleanup() {
    if [ -n "$STUB_PID" ]; then
        kill "$STUB_PID" 2>/dev/null || true
        wait "$STUB_PID" 2>/dev/null || true
    fi
    rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

for tool in asciinema agg python3; do
    command -v "$tool" >/dev/null 2>&1 || {
        echo "record.sh: $tool is not installed" >&2
        exit 1
    }
done

python3 "$DEMO_DIR/stub_server.py" --url-file "$WORK/url" >"$WORK/stub.log" 2>&1 &
STUB_PID=$!

tries=0
while [ ! -s "$WORK/url" ]; do
    tries=$((tries + 1))
    if [ "$tries" -gt 100 ]; then
        echo "record.sh: the stub server never came up; see below" >&2
        cat "$WORK/stub.log" >&2
        exit 1
    fi
    sleep 0.05
done

# Everything the demo needs is an environment variable the shipped adapters
# already read -- the demo requires no product change.
GITHUB_API_URL=$(cat "$WORK/url")
export GITHUB_API_URL
export GITHUB_TOKEN=demo-token
export GITHUB_BOT_LOGIN=tina-demo-bot
export PYTHONPATH="$DEMO_DIR"
# `-m agent` would otherwise leave a demo/__pycache__ behind in the checkout.
export PYTHONDONTWRITEBYTECODE=1
export TINA_DEMO_OUTCOME="$WORK/outcome.json"

if [ -z "${TINA:-}" ]; then
    if command -v tina >/dev/null 2>&1; then
        TINA=tina
    else
        TINA="uv run --quiet --project $ROOT tina"
    fi
fi
export TINA

# The recording runs in a scratch copy of this directory, so `tina.toml` is
# found by default, `cat outcome.json` needs no path, and the checkout stays
# clean.
cp "$DEMO_DIR/tina.toml" "$WORK/tina.toml"
cp -R "$DEMO_DIR/activities" "$WORK/activities"
cd "$WORK"

asciinema rec --overwrite --quiet --headless --return \
    --window-size "${COLS}x${ROWS}" --idle-time-limit 1 \
    --command "sh $DEMO_DIR/demo.sh" "$WORK/demo.cast"

agg --cols "$COLS" --rows "$ROWS" \
    --font-size "$FONT_SIZE" --line-height 1.4 \
    --theme asciinema --fps-cap 10 \
    --idle-time-limit 1 --last-frame-duration 3 \
    "$WORK/demo.cast" "$GIF"

size=$(wc -c <"$GIF" | tr -d ' ')
echo "wrote $GIF ($size bytes)"
if [ "$size" -gt "$BUDGET" ]; then
    echo "record.sh: over the $BUDGET byte budget -- trim a beat or the geometry" >&2
    exit 1
fi
