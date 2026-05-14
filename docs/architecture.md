# Architecture

## What Tina is

Tina is a Python AI agent toolkit. It provides a typed agent runtime, an extensible tool system, and a set of default capabilities — coding tools and an autonomous task loop. The toolkit is designed with swappable components: change the LLM provider, execution environment, tool set, or task source without touching the agent core.

## Three-layer package structure

```
tina-ai          (0 deps on tina)     → LLM abstraction: models, streaming, providers
  ↓
tina-agent       (deps: tina-ai)      → Agent runtime: loop, tools, env, events, sessions, skills
  ↓
tina             (deps: tina-agent)   → Product: CLI, coding tools, integrations, autonomous loop
```

Dependencies flow strictly downward. `tina-ai` is independently useful as a typed LLM client. `tina-agent` is independently useful as an agent runtime. `tina` is the batteries-included product.

## Package responsibilities

### tina-ai

LLM communication abstraction. Defines the types and protocols that the agent layer uses to talk to models without knowing which provider is behind them.

**Defines:**
- `Message`, `Content`, `Usage`, `Cost` — the data types for LLM communication
- `ModelConfig` — static model metadata (provider, pricing, context window, capabilities)
- `StreamEvent`, `StreamResult` — streaming response types
- `Provider` protocol — the swappable interface for LLM backends
- `ToolDefinition` — the schema format sent to LLMs for tool calling
- `ProviderRegistry` — discovers and routes to provider implementations

**Ships:**
- Default `Provider` implementation wrapping Pydantic AI (20+ LLM providers)

**Does not know about:** tools, agents, files, sessions, or any product-level concept.

### tina-agent

Agent runtime and harness. Owns the agent loop — the cycle of prompting the model, executing tool calls, and feeding results back. Defines the protocols that make the runtime extensible.

**Defines:**
- `Tool` protocol — what a tool looks like (name, schema, execute)
- `ToolResult` — what a tool returns (content, details, error flag)
- `ExecutionEnv` protocol — abstract filesystem + shell (local, Docker, SSH)
- `AgentEvent` types — lifecycle events (agent_start, turn_end, tool_exec_end, etc.)
- `HarnessEvent` types — interceptable hooks (before_tool_call, after_tool_call, etc.)
- `EventBus` — two-tier event system (observe + intercept)
- `SessionStorage` protocol — conversation persistence
- `Skill` — model-directed instructions loaded from .md files

**Ships:**
- `AgentHarness` — the main entry point that ties model, tools, env, events, and session together
- Agent loop implementation
- `LocalExecutionEnv` — local filesystem + subprocess
- JSONL session storage
- Skill loader with YAML frontmatter parsing

**Does not know about:** specific tools (bash, file_read), specific integrations, or the autonomous loop.

### tina

The product. Opinionated defaults, specific tools, and the autonomous loop.

**Ships:**
- **Coding tools**: `read`, `write`, `edit`, `bash`, `grep`, `find`, `ls` — each backed by `ExecutionEnv`
- **Autonomous loop**: `TaskSource` protocol and `TaskRunner`
- **CLI**: `tina run` (interactive), `tina auto` (autonomous loop), `tina eval` (evaluation)
- **Configuration**: `TinaSettings` via pydantic-settings (env vars, TOML, CLI flags)
- **`DockerExecutionEnv`** — sandboxed execution for autonomous operation

Integration tools and task source implementations are user-provided. The toolkit provides the protocols; you bring the integrations that fit your workflow.

## Agent loop

The agent loop is the core of `tina-agent`. It is a cycle:

```
Prompt → Model request → Stream response → Extract tool calls → Execute tools → Feed results → Repeat
```

```
                    ┌─────────────────────────────────────────────┐
                    │                                             │
                    ▼                                             │
  Prompt  ──▶  Build context  ──▶  Stream LLM  ──▶  Parse response
                    ▲                                    │
                    │                              ┌─────┴─────┐
                    │                              │           │
                    │                         end_turn    tool_calls
                    │                              │           │
                    │                           (done)    Execute tools
                    │                                      │
                    └──────────────────────────────────────┘
```

The loop continues until the model produces a response with no tool calls (end_turn) or a stop condition is met (max turns, cost limit, abort signal).

At each step, events are emitted to the `EventBus`. Hooks at `before_tool_call` and `after_tool_call` allow extensions to gate, modify, or observe tool execution.

### Steering and follow-up

While the agent is running, external code can inject messages:

- **Steering messages** are injected after the current turn finishes. Used to redirect the agent mid-run.
- **Follow-up messages** are injected after the agent would otherwise stop. Used to chain tasks.

This matches pi's `steer()` and `followUp()` API.

## ExecutionEnv

The `ExecutionEnv` protocol abstracts filesystem and shell operations. Every tool that touches files or runs commands goes through this interface.

```
                  ExecutionEnv (Protocol)
                  ├── cwd: str
                  ├── exec(command) -> ExecResult
                  ├── read_file(path) -> str
                  ├── write_file(path, content)
                  ├── list_dir(path) -> list[FileInfo]
                  ├── exists(path) -> bool
                  ├── mkdir(path)
                  ├── remove(path)
                  └── cleanup()
                        │
              ┌─────────┴─────────┐
              ▼                   ▼
       LocalExecutionEnv    DockerExecutionEnv
       (subprocess, os)     (docker exec, docker cp)
```

Tools receive an `ExecutionEnv` at construction and use it for all operations. They never import `os`, `subprocess`, or `pathlib` directly. This makes every tool automatically sandboxed when the harness uses a sandboxed environment.

## Event system

Two tiers:

- **Listeners** (`bus.on(...)`) — observe events. Return values ignored. Used for logging, metrics, UI.
- **Hooks** (`bus.hook(...)`) — intercept events. Return values modify behavior. Used for permission gates, context injection.

All events are Pydantic models, serializable to JSON for replay and debugging.

## Session persistence

Conversation history is persisted via the `SessionStorage` protocol. The default implementation writes JSONL files — one JSON object per line, each representing a session entry (message, model change, compaction, etc.).

Sessions support:

- **Append-only writes** — entries are appended, never mutated
- **Path-to-root reconstruction** — rebuild the conversation from any point
- **Compaction** — summarize old context to fit within token limits

## Autonomous loop

The autonomous loop is a feature of `tina`, not a core runtime concept. It is a poll-dispatch-report cycle:

```
  ┌──▶  Poll TaskSources  ──▶  Dispatch to AgentHarness  ──▶  Report TaskResult  ──┐
  │                                                                                  │
  └─────────────────────── sleep(poll_interval) ◀────────────────────────────────────┘
```

Each task gets its own `ExecutionEnv` (typically Docker). Tasks are processed concurrently up to a configured limit. Cost budgets prevent runaway spending.

The same `AgentHarness` that powers the interactive CLI powers the autonomous loop. The difference is configuration: which tools are available, which `ExecutionEnv` is used, and whether there's a human at the terminal.

## File structure

```
packages/
├── tina-ai/
│   └── src/tina_ai/
│       ├── __init__.py
│       ├── types.py              # Message, Content, Usage, Cost, StopReason
│       ├── models.py             # ModelConfig, ModelCost
│       ├── stream.py             # StreamEvent, StreamResult protocol
│       ├── provider.py           # Provider protocol, ToolDefinition, StreamOptions
│       ├── registry.py           # ProviderRegistry
│       └── providers/
│           ├── __init__.py
│           └── pydantic_ai.py    # Default Provider wrapping Pydantic AI
│
├── tina-agent/
│   └── src/tina_agent/
│       ├── __init__.py
│       ├── types.py              # AgentMessage, AgentState
│       ├── tools.py              # Tool protocol, ToolResult
│       ├── env.py                # ExecutionEnv protocol, FileInfo, FileError, ExecResult
│       ├── events.py             # AgentEvent, HarnessEvent, EventBus
│       ├── loop.py               # Agent loop implementation
│       ├── session.py            # SessionStorage protocol, SessionEntry
│       ├── skills.py             # Skill model, load_skills(), format_skills_for_prompt()
│       ├── harness.py            # AgentHarness
│       └── envs/
│           ├── __init__.py
│           └── local.py          # LocalExecutionEnv
│
└── tina/
    └── src/tina/
        ├── __init__.py
        ├── cli.py                # Typer CLI: run, auto, eval
        ├── config.py             # TinaSettings (pydantic-settings)
        ├── tools/
        │   ├── __init__.py       # create_coding_tools()
        │   ├── file_read.py
        │   ├── file_write.py
        │   ├── file_edit.py
        │   ├── shell.py
        │   ├── grep.py
        │   ├── find.py
        │   └── ls.py
        ├── auto/
        │   ├── __init__.py
        │   ├── source.py         # TaskSource protocol, Task, TaskResult
        │   └── runner.py         # TaskRunner
        └── envs/
            ├── __init__.py
            └── docker.py         # DockerExecutionEnv
```
