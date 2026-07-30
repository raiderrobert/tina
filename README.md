# Tina

Tina is an autonomous factory. It takes in clear work items from Jira, Github issues, etc and work on them to produce work that's relatively easily verified by a person.

```bash
pip install tina-cli
```


![tina demo](tina-demo.gif)


## How is Tina different from an agent harness? 
Tina is an orchestrator of sort.

It selects a work item, claims it, and calls an agent harness once with a one-shot prompt that I call a _track_.

The agent harness does the individual work item. Currently, I use [pi](https://github.com/earendil-works/pi), because it's small and relatively stable. I may use a different one in the future.

## So what's the value of doing this approach instead?

With an agent harness, you have to sit there and steer it. Even if you have one shot prompts, you need to put those prompts in there.

For work items that are very self similar, you can instead make what I can a _track_.

Think of it like a runbook or a guide. You take several examples of work you have already done once and use them to describe to the agent what it should do.

Then upstream from this system in your ticketing system, you make work items for it.

Downstream from this system PRs get made or Confluence docs get write or whatver other outcome you want.

You just write tickets instead of driving around your agent to get work done.

This approach scales your attention better than having 5 simultaneous agent sessions running simultaenously, especially you enter production situations where you're quickly drowned by the amount of work coming your way. 

## High-level track approach

A track is `Source -> Skill -> Result`.

```toml
harness = "pi"      
executor = "local"

[harnesses.pi]
command = ["pi", "--prompt-file", "{prompt_file}"]

[bug]
source = "github"
repo = "acme/api"
query = "repo:acme/api is:issue is:open no:assignee label:bug"
track = "triage"
result = "github:issue-comment"
```

A basic workflow:

```bash
tina dispatch --track bug --limit 5            
tina run --track bug --item 4821             
tina status --track bug              
```

`status` derives both counts from the track's own `query` — once as `dispatch` runs
it, and once with its `no:assignee` clause swapped for the bot — so they are two
halves of one question and Tina keeps no state to go stale.

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
- **No tracks included.** Tracks are skills, installed with
  [napoln](https://github.com/raiderrobert/napoln) at image build time.

## Documentation

| Document | Description |
|----------|-------------|
| [docs/architecture.md](docs/architecture.md) | System design — tracks, dispatch/worker, adapters, outcome contract, v1 scope |
| [docs/adr/](docs/adr/) | Architecture decision records — the reasoning behind each design choice |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Setup, the check loop, layout, how to add adapters, commit conventions |

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
