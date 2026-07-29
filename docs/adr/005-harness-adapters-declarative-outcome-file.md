# 5. Harness adapters are declarative; outcome arrives by file, not stdout

**Status:** Accepted

**Date:** 2026-07-28

## Context

Every supported agent harness is a CLI. The adapter's job is to build argv, pass
the prompt, run a subprocess, and collect the result — which is small enough that a
code plugin per harness is overkill. The harder half is getting the result back:
each harness prints differently, changes its output between versions, and offers
different `--output-format` flags. Per-harness stdout parsing is where swappability
rots.

## Decision

Harness adapters are subprocess configs templated in TOML, not code plugins:

```toml
[harness.pi]
command = ["pi", "--prompt-file", "{prompt_file}"]

[harness.claude]
command = ["claude", "-p", "@{prompt_file}", "--output-format", "json"]
```

**Tina does not parse harness stdout.** Tina passes an output path in the prompt
and instructs the agent to write `outcome.json` there before finishing. Every
harness can write a file, so the contract is harness-independent. Exit code is the
fallback for "the agent died before writing."

## Consequences

- Adding a harness is a config entry, not a release.
- The contract moves from the harness to the activity: the agent must be told to
  write the file, and an activity that ignores the instruction produces a missing
  outcome.
- A missing `outcome.json` is indistinguishable from a crash. Both resolve to
  `failed` via exit code, which is the correct answer in either case.
- Harness stdout stays useful as a transcript for humans and is never load-bearing.
