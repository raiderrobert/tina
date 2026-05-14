# tina

A Python AI agent toolkit with batteries included and swappable components. Typed protocols all the way down, opinionated defaults you can override, and an autonomous loop for headless operation.

> *"Tina, you fat lard, come get some dinner!"*

- **Three layers.** `tina-ai` (LLM abstraction), `tina-agent` (agent runtime), `tina` (product). Each independently useful.
- **Typed everything.** Pydantic models for data, Python Protocols for interfaces. Every boundary validates.
- **Swappable.** Model provider, execution environment, tool set, task source — change any piece without touching the rest.
- **Sandboxed.** `ExecutionEnv` protocol abstracts file ops and shell. Local for dev, Docker for autonomous. Tools never call `subprocess` directly.
- **Autonomous loop.** Poll Jira, GitHub Issues, or anything else for work. Agent investigates, fixes, opens a PR. Human reviews at the end.
- **Skills.** Load domain knowledge from `.md` files. Same format as Claude Code, pi, Codex, and Gemini CLI.

## Install

Requires Python 3.11+.

```bash
uv pip install tina-agent    # toolkit only
uv pip install tina          # toolkit + CLI + default tools
```

## Quick Start

```python
from tina_ai import ModelConfig, ProviderRegistry
from tina_agent import AgentHarness, LocalExecutionEnv
from tina.tools import create_coding_tools

env = LocalExecutionEnv(cwd=".")
harness = AgentHarness(
    model=ModelConfig(id="claude-sonnet-4-20250514", provider="anthropic", ...),
    provider=ProviderRegistry.default(),
    env=env,
    tools=create_coding_tools(env),
    system_prompt="You are a coding assistant.",
)

messages = await harness.prompt("Fix the failing test in tests/test_auth.py")
```

## CLI

```bash
tina run                          # Interactive coding agent
tina auto                         # Start autonomous loop
tina auto --once VUL-123          # Process a single task
tina auto --dry-run               # Show what would be processed
tina eval dataset.json            # Run eval suite
```

## Autonomous Loop

Configure task sources and let Tina work unsupervised:

```toml
# .tina.toml
[auto]
enabled = true
poll_interval = 60
max_concurrent = 3
cost_limit_per_task = 5.0

[jira]
base_url = "https://mycompany.atlassian.net"
jql_filter = "project = VUL AND status = Open"
```

```bash
tina auto
# ✓ Polling 1 source (jira)
# ✓ VUL-456: Picked up — investigating
# ✓ VUL-456: PR created — https://github.com/org/repo/pull/789
# ✓ VUL-456: Completed in 3m12s ($0.42)
```

Each task gets its own Docker container. Tasks cannot interfere with each other or with the host.

## Packages

| Package | Description |
|---------|-------------|
| **tina-ai** | LLM abstraction — models, streaming, providers. Default wraps Pydantic AI (20+ providers). |
| **tina-agent** | Agent runtime — loop, tools, ExecutionEnv, events, sessions, skills. |
| **tina** | Product — CLI, coding tools, Jira/GitHub integration, autonomous loop. |

`tina-ai` has no dependency on `tina-agent`. `tina-agent` has no dependency on `tina`. Use any layer independently.

## Adding Tools

Tools implement the `Tool` protocol and receive an `ExecutionEnv`:

```python
from tina_agent import Tool, ToolResult, ExecutionEnv, TextContent

class MyTool:
    name = "my_tool"
    description = "Does a thing."
    parameters_schema = {"type": "object", "properties": {"input": {"type": "string"}}}

    def __init__(self, env: ExecutionEnv):
        self._env = env

    async def execute(self, tool_call_id, arguments, **kwargs) -> ToolResult:
        result = await self._env.exec(f"echo {arguments['input']}")
        return ToolResult(content=[TextContent(text=result.stdout)])
```

## Adding Task Sources

Task sources implement the `TaskSource` protocol:

```python
from tina.auto import TaskSource, Task, TaskResult

class WebhookSource:
    source_name = "webhook"

    async def poll(self) -> list[Task]:
        return self._drain_queue()

    async def mark_started(self, task: Task) -> None: ...
    async def mark_completed(self, task: Task, result: TaskResult) -> None: ...
    async def mark_failed(self, task: Task, error: str) -> None: ...
```

## Documentation

- [docs/architecture.md](docs/architecture.md) — System design, layer responsibilities, agent loop, file structure
- [docs/types.md](docs/types.md) — Complete type reference across all three packages
- [docs/autonomous-loop.md](docs/autonomous-loop.md) — Autonomous loop design, isolation, cost control
- [docs/adr/](docs/adr/) — Architecture Decision Records

## License

[MIT](LICENSE)
