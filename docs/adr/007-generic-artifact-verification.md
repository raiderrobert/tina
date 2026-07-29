# 7. Generic artifact verification

**Status:** Accepted

**Date:** 2026-07-28

## Context

Because Tina never writes results ([001](001-orchestration-only-factory.md)), the
agent's report is the only claim that work happened. The dominant failure is an
agent reporting `resolved` with a PR URL it never opened. Catching that properly —
the PR is open, targets the right repo, is non-empty — means a typed verifier per
artifact `kind`, which is a third adapter family alongside sources and executors.

## Decision

When `outcome` is `resolved` and `artifacts` are declared, Tina confirms each
artifact exists with a generic check: HTTP GET each URL using credentials already
in the environment. The other three states have nothing to check.

On mismatch, do not overwrite the agent's report. Record `outcome: resolved` plus
`verified: false`, and flip the effective status to `needs_human`.

Typed per-`kind` verifiers are deferred.

## Consequences

- The dominant failure is caught for very little code, without tripling the v1
  adapter surface to catch failures not yet observed.
- Preserving the agent's original claim alongside `verified: false` is what makes
  it possible to debug a track that lies. Overwriting would destroy the
  evidence.
- A URL that exists but points at an empty or wrong-target PR passes. That is the
  accepted gap until typed verifiers land.
- Tina needs read credentials for result systems, which it does not need today.
  They are already in the image for the agent, so this is env reuse rather than new
  secrets plumbing.
