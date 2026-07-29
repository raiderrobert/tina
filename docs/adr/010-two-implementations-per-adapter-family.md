# 10. Two implementations per adapter family in v1

**Status:** Accepted

**Date:** 2026-07-28

## Context

Tina has three adapter families: sources, executors, and harnesses. Shipping one
implementation of each would be less work, but an interface with a single
implementation is not an interface — it is that implementation's shape with a
protocol drawn around it. A source adapter validated only against Jira ends up
Jira-shaped, and the second tracker is then a rewrite rather than an addition.

## Decision

Every adapter family ships two implementations in v1:

| Family | v1 |
|---|---|
| Sources | Jira, GitHub Issues |
| Executors | `local`, `cloudrun` |
| Harnesses | pi (reference), Claude Code |

pi is the reference harness because it has the smallest feature set, so an adapter
that satisfies it is not relying on harness conveniences.

## Consequences

- Each interface is pulled by two real implementations before it is frozen, so the
  third is an addition.
- The pairs are deliberately unalike: Jira has conditional assignment and GitHub
  does not ([004](004-worker-side-claiming.md)); `local` is a subprocess and
  `cloudrun` is a remote job API.
- GitHub Issues and `local` are also the try-it-without-infra path. An OSS project
  that cannot be run without a Jira instance and a GCP project will not get used.
- More v1 surface to build and test than the minimum.
- Linear, Asana, Kubernetes Jobs, and ECS are deferred, and are additions rather
  than interface changes.
