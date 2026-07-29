# 3. Dispatch/worker split with pluggable executors

**Status:** Accepted

**Date:** 2026-07-28

## Context

One process could query the source and work every matched item in a loop, but that
process runs for as long as the slowest agent run times however many items matched,
which collides with scheduler timeouts and interleaves N agent runs into one log
stream. Scaling it means in-process concurrency, which every platform already
solves better at the job level.

## Decision

Two roles, one image, two CLI subcommands:

```
tina dispatch --track vul --limit 5     # what the scheduler calls
tina run --track vul --item VUL-123     # what the executor spawns; also local dev
```

`dispatch` runs the source query, takes up to N items, and enqueues N worker jobs
through an **executor**. It never runs an agent. `run` handles exactly one work
item: claim, prompt, invoke harness, record.

One item = one run = one container = one log stream. Throughput scales by fanning
out workers, not by concurrency inside a process.

## Consequences

- Dispatcher invocations are short and bounded regardless of how slow agent runs
  are; only workers are long and variable.
- Debugging a single item means reading one container's logs, and reproducing it
  locally is the same `tina run` the executor spawns.
- The executor is an adapter family: `local` (subprocess) and `cloudrun` (job
  execution against the same image) in v1 — see [010](010-two-implementations-per-adapter-family.md).
  Kubernetes Jobs and ECS come later.
- Concurrency limits become the platform's job. Tina exposes `--limit` and nothing
  else.
- Duplicate enqueues are possible, which [004](004-worker-side-claiming.md) makes
  cheap rather than preventing.
