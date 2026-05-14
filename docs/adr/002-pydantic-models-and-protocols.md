# ADR 002: Pydantic Models and Python Protocols as the Type Foundation

**Date**: 2026-05-08
**Status**: Accepted

## Context

Agent code is notoriously type-hostile. Untyped JSON flows between the LLM and tools. Tool results can be anything. Messages are polymorphic unions. Streaming events arrive asynchronously with partial data. Without discipline, the codebase becomes string soup within weeks.

We need a type system that:

1. Validates data at boundaries (LLM responses, tool arguments, config files)
2. Documents interfaces without separate documentation that drifts
3. Enables IDE autocompletion and static analysis
4. Serializes cleanly to/from JSON for persistence and wire formats
5. Defines swappable component interfaces without requiring inheritance

Python offers three main options for structured data: dataclasses (stdlib, no validation), attrs (third-party, optional validation), and Pydantic models (third-party, validation + serialization). For interface contracts, the options are ABC (nominal typing, requires inheritance) and Protocol (structural typing, duck typing).

## Decisions

### 1. Pydantic BaseModel for all data structures

Every data type — messages, content blocks, usage, tool results, config, events, session entries — is a Pydantic `BaseModel`. This gives us:

- Runtime validation at every boundary
- JSON serialization/deserialization via `.model_dump_json()` / `.model_validate_json()`
- JSON Schema generation via `.model_json_schema()` (used directly for LLM tool definitions)
- Discriminated unions via `Literal` type fields

The cost is a Pydantic dependency at every layer, including `tina-ai`. This is acceptable because Pydantic is the de facto standard for typed Python data and is already a transitive dependency via Pydantic AI, pydantic-settings, and most modern Python libraries.

### 2. typing.Protocol for all swappable component interfaces

Every boundary where we want swappability — `Provider`, `ExecutionEnv`, `Tool`, `TaskSource`, `SessionStorage` — is defined as a `typing.Protocol` with `@runtime_checkable`.

Protocols use structural typing (PEP 544): any object that has the right methods and attributes satisfies the protocol, without inheriting from it. This means:

- Third-party implementations don't need to import our base classes
- Testing with mocks is trivial — any object with the right shape works
- No diamond inheritance problems
- `isinstance()` checks still work at runtime via `@runtime_checkable`

### 3. Discriminated unions for message and event types

Message types use a `role` literal field for discrimination:
```python
Message = Union[UserMessage, AssistantMessage, ToolResultMessage]
```

Event types use a `type` literal field:
```python
AgentEvent = Union[AgentStart, AgentEnd, TurnStart, TurnEnd, ...]
```

Content blocks use a `type` literal field:
```python
Content = Union[TextContent, ImageContent, ToolCallContent, ThinkingContent]
```

This pattern enables exhaustive matching, clean serialization, and Pydantic's discriminator optimization.

## Consequences

- Every data boundary in the system validates its inputs. Malformed LLM responses, bad tool arguments, and corrupt session files produce clear errors instead of silent corruption.
- Tool definitions get their JSON Schema from `ToolArgs.model_json_schema()` — no manual schema authoring.
- Config files can be validated at load time with `TinaSettings.model_validate()`.
- Pydantic is a hard dependency at every layer. If Pydantic 3 introduces breaking changes, all three packages are affected.
- Protocols cannot enforce property types or complex invariants at runtime. A class can satisfy `ExecutionEnv` structurally while violating semantic contracts. This is a documentation problem, not a type system problem.
- Pydantic models have overhead compared to plain dataclasses. For hot paths (streaming events, tool result aggregation), this may matter. Profile before optimizing.

## Open Questions

- Should we use `TypedDict` instead of `BaseModel` for lightweight event types that don't need validation?
- Should tool argument models be generated from JSON Schema (for dynamic/MCP tools) or always hand-authored?
