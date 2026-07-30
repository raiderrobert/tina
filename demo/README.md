# The README demos

Two recordings, one entrypoint. The simple demo answers "what is this"; the
volume demo answers "does it hold at scale", which is a question a reader only
asks after the first one worked.

```bash
./demo/record.sh            # tina-demo.gif         — ten bugs, one dispatch
./demo/record.sh volume     # tina-volume-demo.gif  — 1,000 tickets, eight dispatchers
```

The mode picks a session script, a gif, a size budget, the stub's backlog, the
agent's environment and the playback speed. Everything else — stub lifecycle,
the environment Tina reads, terminal geometry, the `agg` flags — is shared, so
the two differ only where they are meant to. An unrecognized argument is an
error naming the two modes.

| | simple | volume |
|---|---|---|
| session | `demo.sh` | `volume.sh` |
| gif | `tina-demo.gif` | `tina-volume-demo.gif` |
| budget | 600,000 bytes | 400,000 bytes |
| backlog | 10 issues | 1,012 issues, 12 held by a human |
| speed | `--speed 1` | `--speed 3` |

Both need [asciinema](https://asciinema.org) and
[agg](https://github.com/asciinema/agg) (`brew install asciinema agg`), `jq` and
`curl`, plus either `tina` on `PATH` or `uv` to run it out of this checkout.
Nothing else, and no network: `record.sh` starts a local stub of the GitHub REST
API and points Tina at it with `GITHUB_API_URL`, so recording the demo cannot
touch a real tracker. Recording it offline produces the same gif.

## The simple demo — four beats

The story is one queue getting worked: ten open bugs in, ten pull requests
waiting for review out.

1. **The queue.** `./queue.sh bugs` — the ten open, unassigned bugs the tracker
   holds. No Tina on screen: this is the view a person would be looking at
   before reaching for anything.
2. **The one command.** `tina dispatch --track bug --limit 10`, piped live
   through `jq` for one line per finished ticket. A real dispatch and ten real
   runs, not `--dry-run`. The pipe is on screen because it is a selling point:
   the log is structured, so it is filterable.
3. **The review queue.** `tina status --track bug` reports `unclaimed: 0` and
   `in flight: 10` off two live tracker queries, and `./queue.sh prs` shows the
   ten pull requests. Humans review them; the factory does not merge its own
   work.
4. **The closer.** A static caption pointing at `--dry-run` and the repo. It is
   a caption and not a command — after beat 2 every issue is claimed, so a
   `no:assignee` preview would match nothing.

Beat 3's `tina status` redirects stdout on screen. Tina's two output streams are
separate by design (`tina.log` owns stdout, `tina.output` owns stderr), and the
counts are the stderr half; sending the JSON stream to `/dev/null` is what keeps
them readable at 98 columns instead of buried under wrapped `httpx` log lines.

## The volume demo — six beats

The story is a 1,000-ticket backlog worked to completion by parallel
dispatchers, ending in a review queue: **967 pull requests open, 33 escalated to
a human**. The mix is the point — the factory escalates rather than pretends.

1. **The backlog.** `./queue.sh bugs --summary` — 1,012 open bugs, 1,000
   unassigned and 12 already held by a person, split across the eight component
   queues the fan-out will follow. Nobody scrolls a thousand rows, so the totals
   are the view: nine numbers out of two real `GET /search/issues` responses,
   then three sample rows.
2. **The fan-out.** Eight concurrent `tina dispatch --track bug-$c --limit 125`
   processes, one per component, each writing its JSON stream to its own file.
   The loop prints the shell's own job pids.
3. **Live progress.** `./progress.py runs-*.jsonl` — a six-line block rewritten
   in place until the fan-out drains, polled off the dispatchers' files and the
   stub's own endpoints. Nothing on it is advanced by a timer.
4. **The end state.** `tina status --track bug-api` reports `unclaimed: 0` and
   `in flight: 125` for one drained shard, and `./queue.sh prs --summary` shows
   the 967 pull requests waiting for review.
5. **The two honesty checks.** `tina run --track bug-api --item 5001` on a
   ticket a human holds: the claim is rejected, the run records
   `no_action_needed`, and no pull request is opened. Then `./audit.py` — eleven
   machine-checked assertions that exit non-zero on the first violation, so the
   closing figures on screen are ones that passed.
6. **The closer.** A caption, not a command. Its elapsed seconds are measured by
   `volume.sh` either side of the fan-out and its "sped up N×" is `$DEMO_SPEED`,
   the same value `record.sh` hands `agg --speed` in that run — neither is a
   literal in the script.

### Why eight tracks and not one

Eight dispatchers against **one shared query** would each take the same
`items[:125]` from the same result and work every ticket eight times. With a
single bot login `GitHubSource.claim` cannot reject the duplicates either: it is
assign-then-reread and GitHub's assign endpoint is an idempotent *add*, so a
second worker assigning the same bot is still the sole assignee and the re-read
passes. Two simultaneous `tina run` workers on one item both resolve and both
open a pull request. That is ADR-004's accepted race, and at eight dispatchers
over one query it is not small.

So exactly-once here comes from the mechanism that provides it in production:
**the query excludes claimed items and the eight dispatchers query disjoint
component slices** — a track per component queue, each on its own schedule
(ADR-002). Nothing in the recording stages a race it pretends to win. The
ADR-004 loser path is shown in beat 5, where it genuinely fires: on a ticket a
*different* login holds, seeded at stub startup rather than produced by a race.

Beat 5's yielding run still leaves its mark. `claim` POSTs the assignee before
it re-reads, so the bot lands on #5001 as the human's co-assignee and is
rejected on the re-read, not on the write. The audit accounts for that rather
than hiding it: what the demo claims is that no ticket was taken *away* from its
holder, and that is the unchanged count of twelve.

The eight dispatchers are also the local stand-in for the production shape. In
production one dispatcher fans out concurrent Cloud Run job executions
(ADR-003, `executor = "cloudrun"`); `LocalExecutor` runs its workers as blocking
subprocesses one after another, so local concurrency has to come from running
eight dispatchers. Beat 6 says so on screen.

## Why a stub

`tina run` claims a work item, and `GitHubSource.claim()` POSTs an assignee to
the tracker. A recording made against a real repo would assign a real issue, so
the demo runs against `stub_server.py` instead. That needs no product change —
only environment variables the shipped adapter already reads:

| Variable | Why |
|----------|-----|
| `GITHUB_API_URL` | Points the source at the stub. Read in `GitHubSource.__init__`. |
| `GITHUB_TOKEN` | `require_env` wants one; any non-empty value does. |
| `GITHUB_BOT_LOGIN` | Skips the `GET /user` lookup of who the token belongs to. |

The URLs in the recording therefore read `http://127.0.0.1:<port>/…`. That is
the honest cost of a demo you can re-record yourself: the run is real, the
tracker is local.

The pull requests are the other half of that, and they are real server state
rather than printed props. `agent.py` POSTs each one to the stub, which assigns
the number itself and hands back an `html_url`; the agent reports that URL and
does not get to invent one. The stub then serves `/{owner}/{name}/pull/{n}` for
those numbers **and 404s every other number**, so `verified: true` in the run
record — computed by `tina.verify` fetching the URL the agent reported — is a
real check that a real pull request exists. An agent naming a plausible URL it
never created comes back `verified: false`, which is exactly the failure
verification exists to catch. `./queue.sh prs` reads the same list back off the
stub, so beat 3 shows what beat 2 actually created.

`audit.py` reads the same endpoints, and only those. Its exactly-once proof is
built out of `GET /search/issues`, `GET /repos/acme/api/pulls` and one
`GET /acme/api/pull/9999` that must 404 — the requests Tina and `queue.sh`
already make. There is no demo-only introspection endpoint, because a check that
needed one would be checking the stub's opinion of itself.

## Files

| File | Role |
|------|------|
| `record.sh` | The entrypoint, in two modes: stub up, record, render, stub down. |
| `demo.sh` | The simple session — four beats, each command echoed then run. |
| `volume.sh` | The volume session — six beats, same convention. |
| `queue.sh` | The tracker views: `bugs` and `prs`, each with a `--summary`. curl and jq only. |
| `progress.py` | Beat 3's six-line in-place progress block, polled off real state. |
| `audit.py` | Beat 5's eleven assertions. Exits non-zero on the first violation. |
| `stub_server.py` | The local GitHub REST API, issues and pull requests. stdlib only. |
| `agent.py` | The fake harness: reads the work item, opens one PR, writes `outcome.json`. |
| `tina.toml` | The simple demo's track: one GitHub source, one fake harness. |
| `tina-volume.toml` | The volume demo's eight component tracks. |
| `tracks/triage/SKILL.md` | Tina ships no tracks; this is one to put in the prompt. |

`record.sh` copies the mode's config in *as* `tina.toml`, along with `queue.sh`,
`tracks/` and — in volume mode — `progress.py` and `audit.py`, into a temporary
directory and records there. So the run leaves nothing behind in the checkout,
the session needs neither a `--config` flag nor a path on `./queue.sh`, and
`tina.toml` cannot regress when the volume demo changes.

`agent.py` writes nothing to stdout. Stdout is Tina's JSON log stream, which
beat 2 pipes through `jq`, and one non-JSON line there aborts the pipe;
diagnostics go to stderr.

## Changing the demo

Terminal geometry, font size, idle-time limit, both gif paths and both size
budgets are pinned at the top of `record.sh`. Nothing outside this directory
needs editing to re-record — the gifs are the only files written elsewhere.

Re-recording the volume demo costs about a minute and a half. Measured on an
M-series laptop:

| step | time |
|---|---|
| stub startup | <1 s |
| fan-out — 1,000 tickets, eight dispatchers, no agent delay | ~50 s |
| the other five beats | ~20 s |
| `agg` render — 63 frames | ~6 s |
| **total** | **~75 s** |

**The accepted budget is ≤3 min per re-record**, which leaves better than 2× for
a slower or busier machine. It is not gated on time — that would fail on CI
hardware for no reason — but `record.sh` prints its own elapsed seconds next to
the byte count, so the cost is observable rather than folklore. The fan-out's
wall clock is the fake harness's real runtime and scales with available cores.
The simple demo is ~22 s.

### If a gif comes in over budget

Gif size is **frames × changed area per frame**, and it is nearly independent of
wall clock and of `--speed`; `--speed` re-times frames, it does not remove them.
The expensive shape is a full-screen repaint, which is what a **scrolling** log
produces: every line on the screen changes every frame. Measured on this
session, beats 4–6 scrolling a full 30-row screen cost nine whole-screen frames
at 22–48 KB each — ~330 KB of a 474 KB gif — against ~2 KB for a frame that only
repaints a few lines.

For the volume demo, in order: raise `progress.py`'s `INTERVAL`; cut its `TAIL`
from three completion lines to one; raise `--speed`, which beat 6's caption
follows automatically; drop the bar line. For the simple demo: drop
`WORK_SECONDS` in `agent.py`, then trim decoration from `queue.sh`.

**Not** levers: `COLS` and `ROWS` — both gifs sit in the same README column, and
geometry changes the rendered dimensions — `--fps-cap` below 8, where the
counter starts reading as broken, and letting beat 3 scroll. The in-place
progress block and the one screen wipe at beat 4 are together why the volume
recording fits at all.
