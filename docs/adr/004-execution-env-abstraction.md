# ADR 004: ExecutionEnv as the Sandboxing Abstraction

**Date**: 2026-05-08
**Status**: Accepted

## Context

An agent that edits files and runs shell commands needs to operate against a filesystem and a shell. In interactive use, that's the local machine. In autonomous operation, that must be a sandboxed environment — a Docker container, a remote VM, or similar — to prevent an unsupervised agent from damaging the host.

Pi solved this with its `ExecutionEnv` interface: an abstract contract for file operations and shell execution that the agent loop calls without knowing what's behind it. The coding-agent provides a local implementation; alternative implementations (Docker, SSH, cloud VMs) can be swapped in without changing the agent loop, tool implementations, or harness.

This is the single most important abstraction for enabling autonomous operation. Without it, every tool that reads a file or runs a command is hardcoded to the local machine, and sandboxing becomes an afterthought bolted on at the wrong layer.

## Decisions

### 1. ExecutionEnv is a Protocol in tina-agent

`ExecutionEnv` is defined as a `typing.Protocol` in `tina-agent`, not in `tina`. This means the agent loop, harness, and all tools in `tina-agent` are sandbox-aware by default. The abstraction is not an optional add-on; it is the foundation.

### 2. The interface covers files and shell execution

`ExecutionEnv` provides:

- **Shell**: `exec(command, cwd, env, timeout) -> ExecResult`
- **File reads**: `read_file(path)`, `read_binary(path)`
- **File writes**: `write_file(path, content)`, `append_file(path, content)`
- **File metadata**: `file_info(path)`, `list_dir(path)`, `exists(path)`
- **File management**: `mkdir(path)`, `remove(path)`, `create_temp_dir()`, `create_temp_file()`
- **Lifecycle**: `cleanup()`

All paths are relative to `cwd` unless absolute. The interface does not include network operations, process management, or anything beyond filesystem and shell — those are tool-level concerns.

### 3. Errors are typed, not exceptions from the OS

File operations raise `FileError` with a stable error code (`not_found`, `permission_denied`, `is_directory`, etc.) rather than propagating OS-specific exceptions. Shell operations raise `ExecError` with codes like `timeout`, `aborted`, `spawn_error`. This ensures tools can handle errors uniformly regardless of whether the backend is local, Docker, or SSH.

### 4. Two default implementations ship with tina

- **`LocalExecutionEnv`** — delegates to the local filesystem and `subprocess`. Used for interactive development and debugging.
- **`DockerExecutionEnv`** — runs commands inside a Docker container. Used for autonomous operation where sandboxing is required.

Both implement the same `ExecutionEnv` protocol. The harness configuration determines which one is used.

### 5. Tools receive ExecutionEnv, not raw paths

Every coding tool (file read, file write, shell, grep, etc.) receives an `ExecutionEnv` at construction time and uses it for all operations. Tools never call `open()`, `os.path`, or `subprocess` directly. This means every tool is automatically sandboxed when the harness uses a sandboxed environment.

## Consequences

- Sandboxing is structural, not bolted on. You cannot accidentally bypass it because the tools don't have access to the local filesystem — they only see what `ExecutionEnv` exposes.
- Testing tools is trivial: provide an in-memory `ExecutionEnv` implementation and test tool behavior without touching the real filesystem.
- The `cleanup()` method ensures resources (Docker containers, temp dirs) are released even when the agent fails.
- The abstraction adds a layer of indirection to every file operation. For interactive use where the backend is local, this is negligible overhead. For Docker-backed execution, the overhead is dominated by the Docker round-trip.
- SSH and cloud VM implementations are not shipped initially but are straightforward to add because the protocol is defined.

## Open Questions

- Should `ExecutionEnv` include a `resolve_path(path)` method that canonicalizes symlinks?
- Should there be a `watch_file(path)` method for file-watching use cases, or is that a separate concern?
- How should `DockerExecutionEnv` handle container lifecycle — create per task, keep warm, use a pool?
