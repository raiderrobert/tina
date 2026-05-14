# ADR 005: Two-Tier Event System: Observe vs. Intercept

**Date**: 2026-05-08
**Status**: Accepted

## Context

The agent harness has many lifecycle points where external code might want to act: before a prompt is sent, when a tool is about to execute, after a tool returns, when the model streams a token, when a session is about to compact. Some of these are purely observational (logging, metrics, UI rendering). Others need to modify behavior (blocking a dangerous tool call, injecting context, overriding a tool result).

Pi handles this with a single event system where some events accept return values. This works but conflates two different contracts: "I want to know about this" vs. "I want to change this." Listeners that only want to observe must still be aware that returning a value could accidentally intercept behavior.

## Decisions

### 1. Two event tiers: listeners and hooks

The `EventBus` supports two registration methods:

- **`on(event_type, fn)`** — registers a listener. The callback receives the event and its return value is ignored. Used for logging, metrics, UI updates, session persistence.
- **`hook(event_type, fn)`** — registers an intercepting hook. The callback receives the event and may return a result that modifies behavior. First non-None return wins. Used for permission gates, context injection, tool result overrides.

### 2. Listener events are the full agent lifecycle

Listener events correspond to the agent loop's natural lifecycle:

- `agent_start`, `agent_end`
- `turn_start`, `turn_end`
- `message_start`, `message_update`, `message_end`
- `tool_exec_start`, `tool_exec_update`, `tool_exec_end`

These events are emitted unconditionally. Listeners cannot prevent or modify them. Multiple listeners are called in registration order.

### 3. Hook events are the harness extension points

Hook events correspond to decision points where behavior can be modified:

- `before_agent_start` → can modify the system prompt or inject messages
- `before_tool_call` → can block the tool call with a reason
- `after_tool_call` → can override the tool result content, error flag, or termination hint
- `before_provider_request` → can modify stream options (headers, timeout)
- `session_before_compact` → can cancel or provide a custom compaction

Each hook event has a corresponding result type that defines what can be modified. Hooks are called in registration order; the first non-None return is used.

### 4. All events are Pydantic models

Every event type — both listener and hook — is a Pydantic `BaseModel` with a `type` literal discriminator field. This means events are serializable (for logging or replay), validatable, and self-documenting.

Hook result types are also Pydantic models with optional fields. Omitted fields mean "don't change the default."

## Consequences

- Observation and interception are clearly separated. A logging extension cannot accidentally block a tool call by returning the wrong thing.
- Hook ordering matters. If two hooks both try to modify the same tool result, the first one wins. This is simple and predictable but may require documentation when composing multiple extensions.
- The two-tier design means two registration methods to learn. This is a small API surface cost for a significant clarity benefit.
- Events are Pydantic models, which means they can be serialized to JSONL for session replay, debugging, and evaluation. An agent run can be fully reconstructed from its event stream.
- Adding a new lifecycle point requires defining a new event model and adding an `emit()` or `emit_hook()` call at the right place in the agent loop. This is straightforward but must be done carefully to avoid breaking existing listeners.

## Open Questions

- Should hooks support async-only or both sync and async callbacks?
- Should there be a priority system for hooks, or is registration order sufficient?
- Should hook events also be emitted to listeners (so observers can see what was intercepted)?
