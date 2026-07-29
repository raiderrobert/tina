# 1. Orchestration-only factory

**Status:** Accepted

**Date:** 2026-07-28

## Context

A factory that takes in work items and produces verifiable output could either
perform the work itself — write files, open PRs, post comments — or hand the work
to an agent that already has tools for all of that. Doing both means Tina
reimplements a result-writing path for every system a workflow targets, and every
new target system becomes Tina's problem.

## Decision

Tina does orchestration only. It selects a work item, claims it, and calls an
agent once with a one-shot prompt containing that item. The agent performs all
work using its own tools. Tina never writes a result.

`result` in a workflow config (`github:pr`, `github:issue-comment`) is a
declaration, not a runtime component. It exists to tell verification what to check
and to tell the image build which credentials it needs.

## Consequences

- Adding a new result system requires no Tina code — only credentials in the image
  and an activity that knows how to use them.
- One activity can produce different results per run. The vulnerability activity
  ends in a PR link or a discovery comment depending on what it finds, so results
  are not 1:1 with workflows.
- Tina cannot guarantee a result was produced. It can only ask the agent what
  happened and check the artifact URL afterward — see
  [007](007-generic-artifact-verification.md).
- Tina never inspects work item content. All judgment about what an item is
  happens inside the activity.
