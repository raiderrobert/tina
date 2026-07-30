#!/bin/sh
# Regenerate a demo gif. One command, no network:
#
#     ./demo/record.sh            tina-demo.gif         ten bugs, one dispatch
#     ./demo/record.sh volume     tina-volume-demo.gif  1,000 tickets, eight dispatchers
#
# Starts the stub tracker on a free port, records the session with asciinema,
# renders the cast with agg, and tears the stub down again -- including when
# something fails partway. Requires asciinema, agg, python3, jq, curl, and
# either `tina` on PATH or `uv` to run it out of this checkout.
#
# The mode picks a session script, a gif, a size budget, the stub's backlog, the
# agent's environment and the playback speed. Everything else -- stub lifecycle,
# the trap, the environment tina reads, terminal geometry, the agg flags -- is
# shared, so the two recordings differ only where they are meant to.
set -eu

DEMO_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH='' cd -- "$DEMO_DIR/.." && pwd)

MODE=${1:-simple}
case "$MODE" in
simple)
    SESSION=demo.sh
    GIF="$ROOT/tina-demo.gif"
    # Measured for this session's shape; see demo/README.md.
    BUDGET=600000
    STUB_ARGS=''
    CONFIG=tina.toml
    # `agg --speed 1` is the default: the simple demo plays at real time,
    # because ten tickets take about as long as they take.
    DEMO_SPEED=1
    ;;
volume)
    SESSION=volume.sh
    GIF="$ROOT/tina-volume-demo.gif"
    # Not inherited from the simple demo: gif size here is frames x changed
    # area, and the in-place progress block is what keeps that small. See
    # demo/README.md for the derivation and the levers if it goes over.
    BUDGET=400000
    # 1,000 workable bugs across eight component queues, plus twelve a human
    # holds. The PR counter starts above every issue number -- issues and pull
    # requests share one number space, and the simple demo's 4900 would collide.
    STUB_ARGS='--issues 1012 --pr-start 6000 --human-held 12'
    CONFIG=tina-volume.toml
    DEMO_SPEED=3
    ;;
*)
    echo "record.sh: unknown mode '$MODE'; expected no argument, or 'volume'" >&2
    exit 2
    ;;
esac

# Exported so the session's closer can name it, and passed to `agg --speed`
# below in the same run -- the caption and the render cannot disagree.
export DEMO_SPEED

# Terminal geometry and timing are pinned here, so two recordings on the same
# machine differ only in timing jitter. Both gifs sit in the same README column,
# so the geometry is shared and is not a size lever.
COLS=98
ROWS=30
FONT_SIZE=14

WORK=$(mktemp -d)
STUB_PID=''
STARTED=$(date +%s)

cleanup() {
    if [ -n "$STUB_PID" ]; then
        kill "$STUB_PID" 2>/dev/null || true
        wait "$STUB_PID" 2>/dev/null || true
    fi
    rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

for tool in asciinema agg python3 jq curl; do
    command -v "$tool" >/dev/null 2>&1 || {
        echo "record.sh: $tool is not installed" >&2
        exit 1
    }
done

# Exported before the stub starts, because the stub reads it too: it is the
# author it reports on the pull requests the agent opens, and the login tina
# claims issues as. One value, so the two halves of the demo agree.
export GITHUB_BOT_LOGIN=tina-demo-bot

# Deliberate word splitting: STUB_ARGS is a flag list, empty in simple mode.
# shellcheck disable=SC2086
python3 "$DEMO_DIR/stub_server.py" --url-file "$WORK/url" $STUB_ARGS >"$WORK/stub.log" 2>&1 &
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
export PYTHONPATH="$DEMO_DIR"
# `-m agent` would otherwise leave a demo/__pycache__ behind in the checkout.
export PYTHONDONTWRITEBYTECODE=1
# The sessions read tina's stdout line by line, and a block-buffered stream
# would arrive as one burst at the end instead of a line per finished ticket.
export PYTHONUNBUFFERED=1

if [ -z "${TINA:-}" ]; then
    if command -v tina >/dev/null 2>&1; then
        TINA=tina
    else
        TINA="uv run --quiet --project $ROOT tina"
    fi
fi
export TINA

# The recording runs in a scratch copy of this directory, so `tina.toml` is
# found by default, the on-screen commands are `./queue.sh` and `./audit.py`
# with no path, and the checkout stays clean. The volume run's config is copied
# in *as* `tina.toml`, so the session needs no `--config` and demo/tina.toml --
# the simple demo's -- cannot regress.
cp "$DEMO_DIR/$CONFIG" "$WORK/tina.toml"
cp "$DEMO_DIR/queue.sh" "$WORK/queue.sh"
cp -R "$DEMO_DIR/tracks" "$WORK/tracks"

if [ "$MODE" = volume ]; then
    cp "$DEMO_DIR/progress.py" "$WORK/progress.py"
    cp "$DEMO_DIR/audit.py" "$WORK/audit.py"
    # Every 30th ticket number comes back `needs_human` instead of resolved --
    # 33 of the 1,000, keyed off the number so the mix is computed per run. Off
    # by default, because one of the simple demo's ten issues is a multiple of
    # 30 and its every-line-`resolved` story is not this one's.
    export TINA_DEMO_ESCALATE_EVERY=30
    # At ten tickets the agent's delay is what makes lines readable; at 1,000
    # the counter carries that, and the delay would add ~44s to every re-record.
    export TINA_DEMO_AGENT_SLEEP=0
fi
cd "$WORK"

asciinema rec --overwrite --quiet --headless --return \
    --window-size "${COLS}x${ROWS}" --idle-time-limit 1 \
    --command "sh $DEMO_DIR/$SESSION" "$WORK/demo.cast"

agg --cols "$COLS" --rows "$ROWS" \
    --font-size "$FONT_SIZE" --line-height 1.4 \
    --theme asciinema --fps-cap 10 --speed "$DEMO_SPEED" \
    --idle-time-limit 1 --last-frame-duration 3 \
    "$WORK/demo.cast" "$GIF"

size=$(wc -c <"$GIF" | tr -d ' ')
echo "wrote $GIF ($size bytes, $(($(date +%s) - STARTED))s)"
if [ "$size" -gt "$BUDGET" ]; then
    echo "record.sh: over the $BUDGET byte budget -- trim a beat or the refresh rate" >&2
    exit 1
fi
