# Tina

> *"Tina, eat. Food. Eat the FOOD!"*

---

An autonomous factory. Tina takes in pre-defined work items and produces work
items that are relatively easily verified by a person — and, with the right
criteria, by an agent.

It is an agent harness with guardrails. Tina does orchestration only: it selects
a work item, claims it, and calls an agent once with a one-shot prompt. The agent
does all the performance, using its own tools.

## How it works

A workflow is `Source -> Activity -> Result`.

```toml
harness = "pi"        # selects [harnesses.pi]; `executor` works the same way
executor = "local"

[harnesses.pi]
command = ["pi", "--prompt-file", "{prompt_file}"]

[vul]
source = "jira"
query = "project = VUL AND status = Open AND assignee IS EMPTY"
activity = "remediate"
result = "github:pr"
```

Two commands, one image:

```bash
tina dispatch --workflow vul --limit 5   # query, take N, enqueue N workers
tina run --workflow vul --item VUL-123   # claim, run the agent, record outcome
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

## Status

Design stage. v1 scope is §18 of the architecture doc.

## Development

```bash
uv sync              # Install all dependencies
uv run pytest        # Run tests
uv run ruff check    # Lint
uv run ty check      # Type check
```

## License

[MIT](LICENSE)
