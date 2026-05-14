# ADR 001: Three-Layer Package Architecture

**Date**: 2026-05-08
**Status**: Accepted

## Context

Tina is an AI agent toolkit that needs to support multiple use cases: interactive coding in a terminal, headless task automation, and autonomous loops that process work items without human intervention. These share a core (agent loop, tool execution, model communication) but diverge at the edges (TUI vs. headless, coding tools vs. integration tools, interactive vs. autonomous).

The pi agent harness (earendil-works/pi) demonstrated a successful layering: `pi-ai` (LLM abstraction) → `pi-agent-core` (agent runtime) → `pi-coding-agent` (product). Each layer is independently useful and independently testable. This architecture allows the community to build alternative products on the same runtime — exactly the Django pattern of a framework that ships with a usable default but doesn't trap you in it.

We also want the model layer to be swappable. Today we use Pydantic AI as the default provider implementation. Tomorrow we might use litellm, raw httpx calls, or a custom provider. This requires a stable interface between the model layer and the agent layer that doesn't leak implementation details.

## Decisions

### 1. Three packages in a uv workspace monorepo

The project is structured as three Python packages:

- **`tina-ai`** — LLM abstraction layer. Defines `Protocol` and Pydantic model types for models, streaming, messages, usage tracking, and provider registration. Ships a default implementation wrapping Pydantic AI. Has no dependency on `tina-agent` or `tina`.
- **`tina-agent`** — Agent runtime and harness. Defines the agent loop, tool protocol, `ExecutionEnv` protocol, event system, session persistence, and skill loading. Depends on `tina-ai` for model types. Has no dependency on `tina`.
- **`tina`** — The product. CLI, coding tools, autonomous loop runner, and configuration. Depends on `tina-agent` and `tina-ai`.

These are separate installable packages in a single monorepo managed by `uv` workspaces.

### 2. Dependency direction is strictly downward

`tina` depends on `tina-agent` depends on `tina-ai`. No package depends on a package above it. No circular references. This is enforced by the package structure itself — if `tina-ai` tried to import from `tina-agent`, the import would fail.

### 3. Each layer is independently useful

- `tina-ai` can be used by anyone who wants a typed, provider-agnostic LLM abstraction in Python without adopting the full agent framework.
- `tina-agent` can be used by anyone who wants an agent runtime with tools and events without adopting Tina's specific coding tools or dark factory.
- `tina` is the opinionated, batteries-included product.

This mirrors Django's layering: you can use the ORM without the views, or the views without the template engine.

## Consequences

- Adding a new LLM provider means touching only `tina-ai`. The agent loop and product layer are unaffected.
- Adding a new tool (e.g., a Kubernetes tool) means touching only `tina`. The agent runtime doesn't need to know about Kubernetes.
- Third parties can build their own products on `tina-agent` without depending on Tina's CLI, autonomous loop, or tool choices.
- The monorepo adds packaging complexity. Version coordination across three packages requires care. We use `uv` workspaces to manage this.
- Three packages means three `pyproject.toml` files, three test suites, three CI configurations. This is a real maintenance cost, justified by the architectural benefit.

## Open Questions

- Should `tina-ai` re-export a convenience `complete()` function (non-streaming) or keep the API streaming-only?
- Should `tina-agent` include a minimal set of default tools (bash, read, write) or should ALL tools live in `tina`?
