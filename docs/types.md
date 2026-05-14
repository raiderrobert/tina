# Core Types Reference

All types are Pydantic `BaseModel` subclasses unless noted as `Protocol`. Discriminated unions use a literal `type` or `role` field.

## tina-ai types

### Content blocks

| Type | Discriminator | Fields | Description |
|------|---------------|--------|-------------|
| `TextContent` | `type="text"` | `text: str` | Plain text |
| `ImageContent` | `type="image"` | `media_type: str`, `data: str` | Base64-encoded image |
| `ToolCallContent` | `type="tool_call"` | `id: str`, `name: str`, `arguments: dict` | Tool call from model |
| `ThinkingContent` | `type="thinking"` | `text: str` | Model reasoning/thinking |

```python
Content = Union[TextContent, ImageContent, ToolCallContent, ThinkingContent]
```

### Messages

| Type | Discriminator | Key fields | Description |
|------|---------------|------------|-------------|
| `UserMessage` | `role="user"` | `content: list[TextContent \| ImageContent]`, `timestamp` | Human or system prompt |
| `AssistantMessage` | `role="assistant"` | `content: list[TextContent \| ToolCallContent \| ThinkingContent]`, `model`, `provider`, `usage`, `stop_reason` | Model response |
| `ToolResultMessage` | `role="tool_result"` | `tool_call_id`, `tool_name`, `content`, `is_error` | Tool execution result |

```python
Message = Union[UserMessage, AssistantMessage, ToolResultMessage]
```

### Usage and cost

| Type | Fields | Description |
|------|--------|-------------|
| `Usage` | `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `total_tokens`, `cost: Cost \| None` | Token and cost tracking per request |
| `Cost` | `input`, `output`, `cache_read`, `cache_write`, `total` | Dollar amounts |

### Stop reasons

```python
StopReason = Literal["end_turn", "tool_use", "max_tokens", "error", "aborted"]
```

### Model configuration

| Type | Fields | Description |
|------|--------|-------------|
| `ModelConfig` | `id`, `name`, `api`, `provider`, `base_url`, `reasoning`, `input_types`, `context_window`, `max_tokens`, `cost_per_million: ModelCost \| None` | Static metadata for a model |
| `ModelCost` | `input`, `output`, `cache_read`, `cache_write` | Per-million-token pricing |

### Streaming

| Type | Discriminator | Fields | Description |
|------|---------------|--------|-------------|
| `TextDelta` | `type="text_delta"` | `delta: str`, `content_index: int` | Incremental text |
| `ToolCallStart` | `type="tool_call_start"` | `id: str`, `name: str` | Tool call begins |
| `ToolCallDelta` | `type="tool_call_delta"` | `id: str`, `delta: str` | Partial JSON args |
| `ThinkingDelta` | `type="thinking_delta"` | `delta: str` | Incremental reasoning |
| `StreamDone` | `type="done"` | `message: AssistantMessage` | Stream complete |
| `StreamError` | `type="error"` | `error: str` | Stream failed |

```python
StreamEvent = Union[TextDelta, ToolCallStart, ToolCallDelta, ThinkingDelta, StreamDone, StreamError]
```

### Protocols

| Protocol | Method | Description |
|----------|--------|-------------|
| `StreamResult` | `__aiter__() -> AsyncIterator[StreamEvent]`, `result() -> AssistantMessage` | Async iterable stream with final result |
| `Provider` | `stream(model, messages, system_prompt, tools, options) -> StreamResult` | LLM backend |

## tina-agent types

### Tool system

| Type | Fields | Description |
|------|--------|-------------|
| `ToolResult` (model) | `content: list[TextContent \| ImageContent]`, `details: Any`, `is_error: bool`, `terminate: bool` | What a tool returns |
| `Tool` (protocol) | `name: str`, `description: str`, `parameters_schema: dict`, `execute(...)` | What a tool is |

### Execution environment

| Type | Fields | Description |
|------|--------|-------------|
| `FileInfo` (model) | `name`, `path`, `kind`, `size`, `mtime_ms` | File metadata |
| `FileError` (exception) | `code: Literal[...]`, `path: str \| None` | Typed file error |
| `ExecResult` (model) | `stdout`, `stderr`, `exit_code` | Shell result |
| `ExecError` (exception) | `code: Literal[...]` | Typed shell error |
| `ExecutionEnv` (protocol) | `cwd`, `exec(...)`, `read_file(...)`, `write_file(...)`, etc. | Abstract filesystem + shell |

### Agent events (observe-only)

| Event | Key fields | Emitted when |
|-------|------------|-------------|
| `AgentStart` | — | Agent loop begins |
| `AgentEnd` | `messages` | Agent loop completes |
| `TurnStart` | — | New model request begins |
| `TurnEnd` | `message`, `tool_results` | Model response + tools complete |
| `MessageStart` | `message` | Message streaming begins |
| `MessageUpdate` | `message`, `event: StreamEvent` | Streaming delta |
| `MessageEnd` | `message` | Message complete, appended to context |
| `ToolExecStart` | `tool_call_id`, `tool_name`, `arguments` | Tool begins executing |
| `ToolExecUpdate` | `tool_call_id`, `tool_name`, `partial_result` | Tool streams partial result |
| `ToolExecEnd` | `tool_call_id`, `tool_name`, `result`, `is_error` | Tool execution complete |

### Harness events (interceptable via hooks)

| Event | Result type | Hook can... |
|-------|-------------|-------------|
| `BeforeAgentStart` | `BeforeAgentStartResult` | Modify system prompt, inject messages |
| `BeforeToolCall` | `BeforeToolCallResult` | Block the call with a reason |
| `AfterToolCall` | `AfterToolCallResult` | Override content, error flag, terminate hint |

### Session

| Type | Fields | Description |
|------|--------|-------------|
| `SessionMetadata` (model) | `id`, `created_at`, `cwd` | Session identity |
| `SessionEntry` (model) | `id`, `parent_id`, `timestamp`, `type`, `data` | One entry in the session log |
| `SessionStorage` (protocol) | `get_metadata()`, `append_entry()`, `get_entries()`, `get_entry()` | Persistence backend |

### Skills

| Type | Fields | Description |
|------|--------|-------------|
| `Skill` (model) | `name`, `description`, `content`, `file_path`, `disable_model_invocation` | Loaded from SKILL.md |

## tina types

### Configuration

| Type | Key fields | Description |
|------|------------|-------------|
| `TinaSettings` | `model`, `sandbox`, `skills`, `auto`, `session_dir` | Root config (pydantic-settings) |
| `ModelSettings` | `provider`, `model_id`, `api_key`, `base_url` | Model selection |
| `SandboxSettings` | `backend`, `docker_image`, `timeout` | Execution environment selection |
| `AutoSettings` | `enabled`, `poll_interval`, `max_concurrent`, `cost_limit_per_task` | Autonomous loop settings |

### Autonomous loop

| Type | Fields | Description |
|------|--------|-------------|
| `Task` (model) | `id`, `source`, `source_ref`, `title`, `description`, `metadata` | Normalized work item |
| `TaskResult` (model) | `task_id`, `success`, `pr_url`, `findings`, `usage`, `error` | Completion report |
| `TaskSource` (protocol) | `source_name`, `poll()`, `mark_started()`, `mark_completed()`, `mark_failed()` | Work intake |
