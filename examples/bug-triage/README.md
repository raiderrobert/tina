# Bug triage — the track in the README recording

## What this is

This is the track running in the recording at the top of the [root
README](../../README.md): open, unassigned bugs in one repo, triaged by one
skill, each ending in a pull request a person reviews. `./demo/record.sh` runs
**these exact files** — not a copy of them. The one difference is a single line:
the recording swaps `harness = "pi"` for a fake harness, because `pi` would want
a real repo and the network. [What the recording adds](#what-the-recording-adds)
says how, and `demo/workdir.sh` fails the recording if anything else differs.

## The track

```toml
harness = "pi"
executor = "local"
tracks_dir = "tracks"

[harnesses.pi]
command = ["pi", "--prompt-file", "{prompt_file}"]

[bug]
source = "github"
repo = "acme/api"
query = "repo:acme/api is:issue is:open no:assignee label:bug"
track = "triage"
result = "github:pr"
```

| Key | What it says |
|---|---|
| `harness = "pi"` | Selects `[harnesses.pi]`. Tina runs that command once per work item and never parses its stdout — the agent writes `outcome.json` to a path Tina passes in the prompt ([architecture §12](../../docs/architecture.md#12-harness-adapters)). |
| `executor = "local"` | `dispatch` enqueues each worker as a local subprocess. `cloudrun` is the other option ([ADR-003](../../docs/adr/003-dispatch-worker-split.md)). |
| `tracks_dir = "tracks"` | Where skills live, resolved relative to this file. Tina ships no tracks; napoln installs them here ([ADR-008](../../docs/adr/008-tracks-installed-via-napoln.md)). |
| `[bug]` | The track's name. `--track bug` selects it. Every top-level table that is not `harnesses` or `executors` is a track. |
| `source = "github"` | The GitHub Issues adapter. `jira` is the other one. |
| `repo = "acme/api"` | Which repo the query and the claim apply to. Required when `source = "github"`. |
| `query` | Run verbatim against the tracker. **`no:assignee` is load-bearing**: claiming assigns the bot, which drops the item out of this query, which is how Tina keeps no state of its own ([ADR-004](../../docs/adr/004-worker-side-claiming.md)). |
| `track = "triage"` | The skill directory under `tracks_dir`. It defaults to the table name; spelled out here because it is the interesting key. |
| `result = "github:pr"` | A declaration, not a runtime component. Tina never writes the result — the agent does, with its own tools. It says which credentials the image needs and what verification should expect ([architecture §4](../../docs/architecture.md#4-tracks)). |

## The skill, and what one run does

The skill is [`tracks/triage/SKILL.md`](tracks/triage/SKILL.md). One work item
goes through it like this:

1. **Dispatch queries and enqueues.** `tina dispatch --track bug --limit 5` runs
   `query` against the tracker, takes up to five items, and enqueues one worker
   per item. The dispatcher never claims and never runs an agent.
2. **The worker claims.** It assigns the bot to the issue. That is what stops two
   workers taking the same item, and it is why `no:assignee` is in the query.
3. **The prompt is assembled** from `SKILL.md`, the work item as JSON, and the
   outcome instructions (`src/tina/prompt.py`). One prompt, one shot.
4. **The harness runs once.** Tina renders `[harnesses.pi]`'s command and waits.
   It does not read the agent's stdout.
5. **The agent writes `outcome.json`** with one of `resolved`,
   `no_action_needed`, `needs_human` or `failed`, free-form `details`, and any
   artifacts it created ([architecture §13](../../docs/architecture.md#13-outcome-contract)).
6. **Tina verifies and records.** Every artifact URL declared under `resolved` is
   fetched to confirm it exists ([architecture §14](../../docs/architecture.md#14-verification)).
   A `resolved` whose artifact does not exist is recorded as `verified: false`,
   which flips the effective status to `needs_human`. The agent's own claim is
   never overwritten — preserving it is what makes a lying track debuggable.

Which of those four states a run ends in is decided **inside the skill**, by the
agent — not by anything in the config. The config picks the track
deterministically off a query; the skill picks the branch
([architecture §7](../../docs/architecture.md#7-two-levels-of-dispatch)). That is
why `SKILL.md` spells out all four exits rather than describing one happy path.

## Running it against your own tracker

Prerequisites: `tina` and the `pi` harness on `PATH`, and `GITHUB_TOKEN` in the
environment.

Two edits to [`tina.toml`](tina.toml). As shipped it names `acme/api`, which does
not exist:

- `repo` — your `owner/name`.
- `query` — the same swap, plus whatever labels you actually use. Keep
  `is:open no:assignee`.

Then the safe first command:

```bash
tina dispatch --track bug --limit 5 --dry-run --config examples/bug-triage/tina.toml
```

The query runs for real against the live tracker, and nothing is claimed and
nothing is enqueued — in `--dry-run` the executor is never constructed at all
(`dispatch_track` in `src/tina/cli.py`), rather than a branch inside the loop
declining to use it.

To see the prompt one item would produce:

```bash
tina run --track bug --item 4821 --dry-run --config examples/bug-triage/tina.toml
```

That writes the genuine prompt to a temp file and prints its path, its size and
the exact harness command line, so you can open the prompt this skill actually
produces. It claims nothing.

**The read-only prefix ends at `--dry-run`.** Dropping it assigns real issues to
your bot and runs your agent against your repo.

## What the recording adds

The recording needs a tracker it may mutate and an agent that finishes in under a
second, so [`demo/`](../../demo/README.md) supplies a local stub of the GitHub
REST API and a fake harness. Neither is part of the track and neither is worth
copying — that is why they are not in this directory.

The substitution is exactly one line:

```
harness = "pi"   ->   harness = "demo"
```

plus a `[harnesses.demo]` table appended from `demo/overlay.toml`. `demo/workdir.sh`
then reverses the substitution and diffs the result against this directory's
`tina.toml`; if they differ by anything at all, it exits non-zero and no gif gets
made. `just check` runs that assertion on every pull request, so the recording
cannot quietly stop being evidence for what is published here.
