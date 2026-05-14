# Tina

> *"Tina, you fat lard, come get some dinner!"*

---

# Tina Agent Toolkit Mono Repo

This is the home of the Tina agent toolkit — a Python AI agent harness with batteries included and swappable components.

* **[tina](packages/tina)**: CLI, coding tools, integrations, and autonomous loop
* **[tina-agent](packages/tina-agent)**: Agent runtime with tool calling, events, sessions, and skills
* **[tina-ai](packages/tina-ai)**: Typed multi-provider LLM API (Anthropic, OpenAI, Google, …)

## All Packages

| Package | Description |
|---------|-------------|
| **[tina-ai](packages/tina-ai)** | LLM abstraction — models, streaming, provider protocol. Default wraps Pydantic AI. |
| **[tina-agent](packages/tina-agent)** | Agent runtime — loop, tool protocol, ExecutionEnv, event bus, sessions, skills |
| **[tina](packages/tina)** | Product — CLI, coding tools, Jira/GitHub integration, autonomous task loop |

Dependencies flow strictly downward. `tina-ai` has no dependency on `tina-agent`. `tina-agent` has no dependency on `tina`. Each layer is independently useful.

## Key Ideas

- **Typed everything.** Pydantic models for data, Python Protocols for interfaces. Every boundary validates.
- **Swappable.** Model provider, execution environment, tool set, task source — change any piece without touching the rest.
- **Sandboxed.** `ExecutionEnv` protocol abstracts file ops and shell. Local for dev, Docker for autonomous. Tools never call `subprocess` directly.
- **Two-tier events.** Listeners observe (logging, metrics, UI). Hooks intercept (permission gates, context injection, tool overrides).
- **Autonomous loop.** Poll Jira, GitHub Issues, or any `TaskSource` for work. Agent investigates, fixes, opens a PR. Human reviews at the end.
- **Skills.** Load domain knowledge from `.md` files.

## Documentation

| Document | Description |
|----------|-------------|
| [docs/architecture.md](docs/architecture.md) | System design — layers, agent loop, ExecutionEnv, events, file structure |
| [docs/types.md](docs/types.md) | Complete type reference across all three packages |
| [docs/autonomous-loop.md](docs/autonomous-loop.md) | Autonomous loop — task sources, runner, isolation, cost control |
| [docs/adr/](docs/adr/) | Architecture Decision Records — the why behind each design decision |

## Development

```bash
uv sync              # Install all dependencies
uv run pytest        # Run tests
uv run ruff check    # Lint
uv run mypy          # Type check
```

## License

[MIT](LICENSE)
