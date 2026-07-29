# Tina

> *"Tina, eat. Food. Eat the FOOD!"*

---

An autonomous factory. Tina takes in pre-defined work items and produces work
items that are relatively easily verified by a person — and, with the right
criteria, by an agent.

It is an agent harness with guardrails. Tina does orchestration only: it selects
a work item, claims it, and calls an agent once with a one-shot prompt. The agent
does all the performance, using its own tools.

![tina demo](tina-demo.gif)

## How it works

A workflow is `Source -> Activity -> Result`.

```toml
harness = "pi"        # selects [harnesses.pi]; `executor` works the same way
executor = "local"

[harnesses.pi]
command = ["pi", "--prompt-file", "{prompt_file}"]

[bug]
source = "github"
repo = "acme/api"
query = "repo:acme/api is:issue is:open no:assignee label:bug"
activity = "triage"
result = "github:issue-comment"
```

Two commands, one image:

```bash
tina dispatch --workflow bug --limit 5             # query, take N, enqueue N workers
tina dispatch --workflow bug --limit 5 --dry-run   # preview the matches, enqueue nothing
tina run --workflow bug --item 4821                # claim, run the agent, record outcome
```

An external scheduler calls `dispatch`. Tina does not own scheduling — Cloud
Scheduler, EventBridge, k8s CronJob, systemd timers, and GitHub Actions all work
without Tina knowing about them.

## Design

- **Orchestration only.** Tina never writes a result. The agent does, with its
  own tools.
- **Harness agnostic.** pi, Claude Code, and others are subprocess adapters. Tina
  never parses harness stdout — the agent writes `outcome.json` to a path Tina
  provides.
- **No persistent state.** The tracker is the ledger. Workers claim items, and
  claimed items drop out of the query.
- **No activities included.** Activities are skills, installed with
  [napoln](https://github.com/raiderrobert/napoln) at image build time.

## Documentation

| Document | Description |
|----------|-------------|
| [docs/architecture.md](docs/architecture.md) | System design — workflows, dispatch/worker, adapters, outcome contract, v1 scope |
| [docs/adr/](docs/adr/) | Architecture decision records — the reasoning behind each design choice |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Setup, the check loop, layout, how to add adapters, commit conventions |

## Status

Implemented, pre-release. Not yet published to PyPI. v1 scope is §18 of the architecture doc.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full guide.

[just](https://github.com/casey/just) is the task runner (`brew install just`).

```bash
just setup    # Install all dependencies
just check    # Format check, lint, type check, tests
just fmt      # Auto-fix formatting and lint
just test     # Run tests
```

Without just:

```bash
uv sync --all-extras       # Install all dependencies
uv run ruff format --check # Format check
uv run ruff check          # Lint
uv run ty check            # Type check
uv run pytest              # Run tests
```

## License

[MIT](LICENSE)
