# 9. Python

**Status:** Accepted

**Date:** 2026-07-28

## Context

Tina is a CLI that shells out to another CLI, so the usual argument for Go — a
single static binary with no runtime to install — is the main thing worth weighing.

## Decision

Tina is written in Python.

## Consequences

- The static-binary advantage is void. The image must already carry the agent
  harness and the activity tooling, so it is never a single-binary image regardless
  of what Tina is written in.
- napoln is Python, so `uvx napoln` is already present in the consumer image
  ([008](008-activities-installed-via-napoln.md)). Tina adds no new runtime.
- Tina inherits Python startup cost on every worker invocation. Irrelevant next to
  an agent run.
- Distribution is a container image, not a downloadable binary. Anyone wanting to
  run Tina outside a container needs a Python environment.
