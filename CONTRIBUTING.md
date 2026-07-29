# Contributing to Tina

## Setup

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), and
[just](https://github.com/casey/just).

```bash
brew install uv     # or: curl -LsSf https://astral.sh/uv/install.sh | sh
brew install just
```

```bash
git clone git@github.com:raiderrobert/tina.git
cd tina
just setup
```

`just setup` runs `uv sync --all-extras`. All extras, not just the dev group:
`just types` needs the `cloudrun` extra resolvable, or `ty` reports an
unresolved import on `from google.cloud import run_v2` in
`src/tina/executors/cloudrun.py`.

Running Tina by hand needs adapter credentials in the environment. See
[.env.example](.env.example).

## The check loop

Run `just check` before every commit. It is the gate CI runs —
`.github/workflows/ci.yml` calls `just setup`, then `just check`, then
`just build`, on a 3.12/3.13 matrix.

`just check` runs four recipes in order:

| Recipe | Command |
|--------|---------|
| `fmt-check` | `uv run ruff format --check` |
| `lint` | `uv run ruff check` |
| `types` | `uv run ty check` |
| `test` | `uv run pytest` |

```bash
just fmt            # auto-fix: ruff format, then ruff check --fix
just test -k jira   # arguments pass through to pytest
just test -x
just coverage       # test run with a coverage report
```

Also `just build` (`uv build`) and `just clean` (remove build artifacts and
caches). `just` with no recipe lists them all.

## Project layout

```
src/tina/
├── cli.py           # `tina dispatch` and `tina run` — two roles, one image
├── __main__.py      # `python -m tina`, how the local executor spawns workers
├── config.py        # TOML configuration
├── models.py        # Work items in, outcome reports out
├── prompt.py        # One-shot prompt assembly
├── harness.py       # Harness invocation
├── verify.py        # Generic artifact verification
├── log.py           # Structured logging, one JSON object per line
├── errors.py        # TinaError — the one thing the CLI catches
├── sources/         # Where work items come from, and how they get claimed
│   ├── base.py      # Source protocol, require_env, parse_payload
│   ├── jira.py
│   └── github.py
└── executors/       # How the dispatcher enqueues workers
    ├── base.py      # Executor protocol
    ├── local.py
    └── cloudrun.py

tests/                 # conftest.py + unit/ (per-module) + integration/ (through the CLI)
docs/architecture.md   # System design
docs/adr/              # Architecture decision records
examples/tina.toml     # Worked configuration
Dockerfile.reference   # Reference consumer image; documentation, not a build
justfile               # Every check, in one place
```

## Adding a source adapter

1. Implement the `Source` protocol in `src/tina/sources/base.py` — `query`,
   `get`, `claim`. Use `require_env` and `parse_payload` from the same module
   for credentials and response validation.
2. Register it in the `build()` dispatch in `src/tina/sources/__init__.py`.
3. Add `tests/unit/test_sources_<name>.py`, modeled on `test_sources_jira.py` and
   `test_sources_github.py`.

`JiraSource` and `GitHubSource` are the templates. Copy their shape.

## Adding an executor

1. Implement the `Executor` protocol in `src/tina/executors/base.py` —
   `enqueue`.
2. Register it in `build()` in `src/tina/executors/__init__.py`.
3. Add cases to `tests/unit/test_executors.py`, modeled on the existing `local` and
   `cloudrun` tests.

`LocalExecutor` and `CloudRunExecutor` are the templates.

A harness is not an adapter in this sense: harnesses are configured, not coded.
A new one is a `[harnesses.<name>]` block with a `command`, not a new module
(§12 of [docs/architecture.md](docs/architecture.md)).

## Tests

No test touches the network. HTTP is faked with `httpx.MockTransport` through
the `make_client` fixture in `tests/conftest.py`.

The autouse `clean_env` fixture strips real credentials from the environment. A
test that needs a credential sets it with `monkeypatch.setenv`, and a new
credential variable gets added to `clean_env`'s list.

`work_item` is the shared `WorkItem` sample.

`tests/unit/` holds one `test_*.py` per module. `tests/integration/` holds
tests that drive tina through the composition seam — a real on-disk config,
source, prompt, harness subprocess, and verification in one run. Shared
fixtures stay in `tests/conftest.py`.

There is no `tests/fixtures/` directory. Tests generate the configuration they
need per case — each `tina.toml` variant *is* the assertion — so a committed
file would only put the input a directory away from the test that explains it.

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/).
release-please reads commit history to compute the version and generate the
changelog (`.github/workflows/release-please.yml`,
`release-please-config.json`); a non-conforming commit silently drops out of
the release notes.

```
feat(cli): add a --version flag
fix(tests): strip ANSI styling before asserting on help output
chore: add a justfile as the single source of truth for checks
docs: lead README example with the github bug workflow
ci: run checks on a 3.12/3.13 matrix and make ty blocking
refactor: validate boundaries with pydantic models
```

Never add `Co-Authored-By` or any other AI-attribution trailer to a commit.

Work lands directly on `main`. There is no fork, feature branch, or review
step. `just check` green is the gate.

## Design decisions

[docs/architecture.md](docs/architecture.md) and [docs/adr/](docs/adr/) are
authoritative. Read them before proposing a structural change.

Invariants a change must not break, each with the record that argues it:

| Invariant | ADR |
|-----------|-----|
| Orchestration only — Tina never writes a result | [001](docs/adr/001-orchestration-only-factory.md) |
| Tina does not own scheduling | [002](docs/adr/002-no-scheduling-ownership.md) |
| Dispatch and worker are separate roles | [003](docs/adr/003-dispatch-worker-split.md) |
| Claiming is worker-side; the tracker is the ledger | [004](docs/adr/004-worker-side-claiming.md) |
| Tina never parses harness stdout — the agent writes `outcome.json` | [005](docs/adr/005-harness-adapters-declarative-outcome-file.md), [006](docs/adr/006-four-state-outcome-contract.md) |
| Every adapter family ships two implementations | [010](docs/adr/010-two-implementations-per-adapter-family.md) |

New ADRs copy [docs/adr/000-adr-template.md](docs/adr/000-adr-template.md) and
are numbered sequentially.

## License

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENSE).
