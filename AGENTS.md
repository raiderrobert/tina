# Development Rules

## First Message

If no concrete task is given, read README.md and docs/architecture.md, then ask
what to work on.

## Project Overview

Tina is an orchestration-only factory: work items in, outcome reports out. It
claims a work item and calls an agent once with a one-shot prompt; the agent
does the work. Python 3.12+, uv and just, ruff/ty/pytest. Two roles, one image:
`tina dispatch` queries and enqueues workers, `tina run` claims one item and
records its outcome. Two adapter families: `sources/` (work item origins) and
`executors/` (worker enqueueing). Read [CONTRIBUTING.md](CONTRIBUTING.md) for
setup, layout, and how to add an adapter.

## The Check Gate

- Run `just check` after code changes. Do not claim a change is done, working,
  or ready to commit without a green `just check` in this session. CI runs the
  same command, so a local green is the whole contract.
- `just fmt` auto-fixes formatting and lint.
- `just test -k <expr>` narrows while iterating, but a narrowed run is not the
  gate.

## Invariants

[docs/architecture.md](docs/architecture.md) and [docs/adr/](docs/adr/) are
authoritative; each rule below is argued in the record it links.

- Tina never parses harness stdout; the agent writes `outcome.json`
  ([005](docs/adr/005-harness-adapters-declarative-outcome-file.md),
  [006](docs/adr/006-four-state-outcome-contract.md)).
- Orchestration only — Tina never writes a result
  ([001](docs/adr/001-orchestration-only-factory.md)).
- Claiming is worker-side; the tracker is the ledger
  ([004](docs/adr/004-worker-side-claiming.md)).
- Every adapter family ships two implementations
  ([010](docs/adr/010-two-implementations-per-adapter-family.md)).
- A harness is configuration — a `[harnesses.<name>]` block with a `command`,
  not a new module. Do not add a harness adapter class (§12 of
  [docs/architecture.md](docs/architecture.md)).

## Commits

- Use [Conventional Commits](https://www.conventionalcommits.org/).
  release-please reads the history, so a non-conforming subject silently drops
  out of the changelog. Examples are in [CONTRIBUTING.md](CONTRIBUTING.md).
- NEVER add `Co-Authored-By` or any other AI-attribution trailer.
- Work lands directly on `main` — no fork, no feature branch, no review step.
  Do not open a pull request or create a branch for this repo unless asked.

## Tests

- The autouse `clean_env` fixture in `tests/conftest.py` deletes real adapter
  credentials from the environment. A test that fails on a missing credential
  is correct behavior: set it with `monkeypatch.setenv`. Never weaken, opt out
  of, or delete `clean_env`, or export real credentials to make a test pass.
- A new credential variable must be added to `clean_env`'s tuple.
- No test touches the network. Fake HTTP through the `make_client` fixture
  (`httpx.MockTransport`). Do not add `pytest-httpx`, `responses`, `vcr`, or
  live-network tests.
- Unit tests (one file per module) go in `tests/unit/`; tests that drive the
  full CLI stack go in `tests/integration/`. There is no `tests/fixtures/`.

## Real Trackers

`tina dispatch` and `tina run` with real `JIRA_*` or `GITHUB_*` credentials
claim live work items and comment on real issues. Never run either against a
real tracker without explicit instruction; see [.env.example](.env.example).

## Style

- Concise answers, no filler.
- Neutral technical voice in documentation: no editorializing, no first-person
  plural.
- No emojis in commits, docs, or code.
- Use the read tool to examine files, not `cat` or `sed`; read fully before
  editing.
