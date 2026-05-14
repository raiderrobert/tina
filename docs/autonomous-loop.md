# Autonomous Loop

## What it is

The autonomous loop is a headless mode where Tina polls for work items, processes each one with an agent, and reports results — without human intervention during execution. A human reviews the output after the agent finishes, not while it’s working.

This is one capability of the toolkit, not the defining feature. The same `AgentHarness` that powers the interactive CLI powers the autonomous loop. The difference is configuration: which tools are loaded, which `ExecutionEnv` is used, and whether there's a person at the terminal.

## When to use it

The autonomous loop is appropriate for work that is:

- **Well-scoped**: the task has a clear input (a ticket, an issue) and a clear output (a PR, a comment, a report)
- **Repeatable**: the same class of task comes up regularly (dependency updates, bug triage, security patches)
- **Reviewable**: a human can verify the output faster than they could produce it
- **Bounded**: the cost and risk of a single task are limited (one repo, one PR, bounded token budget)

It is not appropriate for exploratory work, architectural decisions, or tasks where the human needs to steer the agent interactively.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          TaskRunner                                 │
│                                                                     │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐                       │
│  │ Source A │   │ Source B │   │ Source C │    ◀── TaskSource      │
│  │          │   │          │   │          │        Protocol         │
│  └────┬─────┘   └────┬─────┘   └────┬─────┘                       │
│       │              │              │                               │
│       └──────────────┼──────────────┘                               │
│                      ▼                                              │
│               poll() → list[Task]                                   │
│                      │                                              │
│                      ▼                                              │
│          ┌───────────────────────┐                                  │
│          │   For each task:      │                                  │
│          │                       │                                  │
│          │   1. mark_started()   │                                  │
│          │   2. Create env       │──▶  DockerExecutionEnv           │
│          │   3. Build harness    │──▶  AgentHarness                 │
│          │   4. Run agent        │──▶  harness.prompt(task_prompt)  │
│          │   5. Extract result   │                                  │
│          │   6. mark_completed() │                                  │
│          │      or mark_failed() │                                  │
│          │   7. env.cleanup()    │                                  │
│          └───────────────────────┘                                  │
│                      │                                              │
│                      ▼                                              │
│            sleep(poll_interval)                                     │
│                      │                                              │
│                      └──────── loop ◀───────────────────────────    │
└─────────────────────────────────────────────────────────────────────┘
```

### Components

**TaskSource** (protocol) - provides work items. Each source owns its polling state, its connection to the external system, and its reporting format. Implementing a new source means writing one class with four methods.

**Task** — a normalized work item. Regardless of origin, every task has the same shape: id, source, title, description, metadata. The runner and agent prompt work against this one type.

**TaskRunner** - the orchestrator. Polls sources, dispatches tasks to agent harnesses, enforces concurrency and cost limits, and reports results back to sources. It is deliberately simple: no priority queues, no retry logic, no scheduling beyond a poll interval. Those are application-level concerns that can be built on top.

**TaskResult** - the output of a completed task. Contains success/failure, PR URL (if created), human-readable findings, and usage data. The source implementation decides how to deliver this to the external system.

## Task lifecycle

```
                          New
                           │
                    poll() returns task
                           │
                           ▼
                    ┌─────────────┐
    mark_started()  │  In Progress │
                    └──────┬──────┘
                           │
                     agent runs
                           │
                  ┌────────┴────────┐
                  ▼                 ▼
           ┌────────────┐   ┌────────────┐
           │  Completed  │   │   Failed   │
           └────────────┘   └────────────┘
        mark_completed()     mark_failed()
```

The source implementation maps these transitions to the external system's workflow. For example, an issue tracker source might transition the ticket and add a comment. A chat source might react with an emoji and reply in a thread.

## Isolation

Each task runs in its own `ExecutionEnv`. In autonomous mode, this is typically a `DockerExecutionEnv`:

1. A fresh container is created (or pulled from a warm pool) for the task
2. The target repository is cloned into the container
3. The agent runs inside the container - all file operations and shell commands are scoped to it
4. On completion (success or failure), the container is destroyed via `env.cleanup()`

Tasks cannot interfere with each other or with the host. If the agent runs `rm -rf /`, it destroys the container, not the machine.

## Cost control

The runner enforces cost budgets at two levels:

- **Per-task limit** (`cost_limit_per_task`) - if a single task's token usage exceeds the budget, the agent is aborted. The task is marked as failed with the reason.
- **Concurrency limit** (`max_concurrent`) - at most N tasks run simultaneously. Additional tasks wait for a slot.

Cost tracking uses the `Usage` model from `tina-ai`, which includes token counts and dollar amounts per request.

## Prompt construction

When the runner dispatches a task, it builds a prompt from the `Task` model:

```
You are resolving a task from {task.source}.

Task: {task.source_ref}
Title: {task.title}

{task.description}

{source-specific metadata}

Investigate, propose a fix, and create a pull request.
Leave your findings as a comment on the original ticket.
```

The exact prompt template is configurable. Skills loaded from `.md` files provide domain-specific instructions (e.g., "how to update a Maven dependency," "how to triage a PHP error").

## Implementing a source

Implement the `TaskSource` protocol:

```python
class MyTaskSource:
    source_name = "my_source"

    async def poll(self) -> list[Task]:
        # Check for new work items, return as normalized Tasks
        ...

    async def mark_started(self, task: Task) -> None:
        # Signal that work has begun
        ...

    async def mark_completed(self, task: Task, result: TaskResult) -> None:
        # Report success back to the source system
        ...

    async def mark_failed(self, task: Task, error: str) -> None:
        # Report failure back to the source system
        ...
```

Register it with the runner:

```python
runner = TaskRunner(
    sources=[MyTaskSource(...)],
    harness_factory=my_harness_factory,
)
await runner.run_forever()
```

No changes to the runner, agent, or tools.

## CLI

```bash
# Start the autonomous loop
tina auto

# Process a single task (for testing)
tina auto --once TASK-123

# Dry run - show what would be processed without running agents
tina auto --dry-run
```

## Relationship to interactive mode

The autonomous loop and interactive CLI share:

- The same `AgentHarness` class
- The same tool implementations (if configured)
- The same event system (an observer could log both interactive and autonomous runs identically)
- The same session storage (autonomous runs produce the same session JSONL as interactive runs)

They differ in:

| Aspect | Interactive (`tina run`) | Autonomous (`tina auto`) |
|--------|--------------------------|--------------------------|
| Input | Human types a prompt | TaskSource provides a Task |
| Env | LocalExecutionEnv | DockerExecutionEnv |
| Human | Present, can steer | Absent, reviews after |
| Output | Terminal | PR + ticket comment |
| Cost | Unbounded (human decides) | Budgeted per task |
| Concurrency | 1 | Configurable |
