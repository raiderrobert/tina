# ADR 006: Autonomous Loop Task Source Protocol

**Date**: 2026-05-08
**Status**: Accepted

## Context

Tina includes an autonomous loop — a headless mode where the toolkit polls for work items, processes them with an agent, and reports results without human intervention. This supports use cases like vulnerability remediation, bug triage, dependency updates, or any work that can be expressed as a task with a clear input and reviewable output.

The autonomous loop needs to be agnostic about where work comes from. Sources could be issue trackers, message queues, webhook endpoints, email, or a local file queue. The loop should not know or care about the source — it just receives typed tasks and reports results.

## Decisions

### 1. TaskSource is a Protocol

Work intake is defined by a `TaskSource` protocol with four methods:

- `poll() -> list[Task]` — returns new tasks since the last poll
- `mark_started(task)` — signals that work has begun
- `mark_completed(task, result)` — signals success
- `mark_failed(task, error)` — signals failure

Each source implementation owns its polling state (last seen timestamp, processed IDs), its reporting format, and its connection to the external system. The runner does not manage these details.

No source implementations are baked into the toolkit. Users implement this protocol for their specific systems.

### 2. Task is a normalized Pydantic model

Regardless of source, every work item is normalized into a `Task` model with:

- `id` — unique within the source
- `source` — source name (e.g., `"my_tracker"`, `"email"`)
- `source_ref` — source-specific reference (e.g., `"PROJ-123"`, `"msg-456"`)
- `title` — human-readable summary
- `description` — full description
- `metadata` — source-specific data (labels, priority, assignee, etc.)

This normalization means the runner, agent prompt construction, and result reporting all work against one type regardless of source.

### 3. TaskResult is the output contract

Every completed task produces a `TaskResult`:

- `task_id` — which task this resolves
- `success` — whether the agent completed the work
- `pr_url` — if a PR was created
- `findings` — human-readable summary of what was done
- `usage` — token/cost usage for the run
- `error` — error message if failed

The source implementation decides how to format and deliver this result back to the originating system.

### 4. The runner is a simple poll-dispatch loop

`TaskRunner` polls all registered sources, dispatches tasks to agent harness instances, and reports results back to the source. It manages concurrency limits and cost budgets. It does not manage retries, scheduling, or complex orchestration — those are application-level concerns.

The runner can operate in two modes:
- `run_forever()` — continuous polling loop, runs until cancelled
- `run_once(task)` — process a single task, useful for testing and CLI

### 5. Each task gets its own ExecutionEnv

When processing a task, the runner creates a fresh `ExecutionEnv` (typically Docker) for the agent to work in. This ensures tasks are isolated from each other and from the host. The environment is cleaned up after the task completes, regardless of success or failure.

## Consequences

- Adding a new work source means implementing one class with four methods. No changes to the runner, agent, or tools.
- The `Task` normalization loses source-specific richness. A ticket in an issue tracker has fields, components, sprint membership; a chat message has thread context. This richness is available in `metadata` but not in the typed fields. Agent prompts may need to dip into `metadata` for source-specific context.
- The runner is intentionally simple. It does not handle complex scheduling (priority queues, rate limiting per source, backoff on repeated failures). These can be built on top of the runner or by wrapping the `TaskSource` implementations.
- The poll-based model adds latency (up to one poll interval) compared to a webhook/push model. A future `TaskSource` could implement push semantics internally while still exposing the same `poll()` interface (returning items from an internal queue populated by webhooks).
- No source implementations ship with the toolkit. This keeps the core dependency-free from any specific external service client, but means users must write their own source before the autonomous loop does anything.

## Open Questions

- Should the runner support task prioritization, or is that a source-level concern?
- Should failed tasks be retried automatically, and if so, with what backoff strategy?
- Should there be a `TaskSource.acknowledge(task)` method separate from `mark_started` for exactly-once processing guarantees?
