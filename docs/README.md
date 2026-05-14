# Tina Documentation

Tina is a Python AI agent toolkit with batteries included and swappable components. It provides a typed agent runtime, an extensible tool system, and a set of default capabilities including coding tools, integration tools, and an autonomous task loop. Named after the llama from Napoleon Dynamite.

## Documents

| Document | Description |
|----------|-------------|
| [architecture.md](architecture.md) | System design: three-layer package structure, protocols, agent loop, event system, and execution environments |
| [types.md](types.md) | Core type reference: messages, content blocks, usage, streams, tools, events, sessions, and skills |
| [autonomous-loop.md](autonomous-loop.md) | Autonomous loop design: task sources, runner, dispatch, and headless operation |
| [adr/](adr/) | Architecture Decision Records — the why behind each major design decision |

## Architecture Decision Records

| ADR | Title |
|-----|-------|
| [001](adr/001-three-layer-package-architecture.md) | Three-Layer Package Architecture |
| [002](adr/002-pydantic-models-and-protocols.md) | Pydantic Models and Python Protocols as the Type Foundation |
| [003](adr/003-pydantic-ai-as-default-provider.md) | Pydantic AI as the Default Provider Implementation |
| [004](adr/004-execution-env-abstraction.md) | ExecutionEnv as the Sandboxing Abstraction |
| [005](adr/005-event-system-design.md) | Two-Tier Event System: Observe vs. Intercept |
| [006](adr/006-autonomous-loop-task-protocol.md) | Autonomous Loop Task Source Protocol |
