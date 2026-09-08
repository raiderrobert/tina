# 18. Structured query inputs build the query; `query` remains the override

**Status:** Partially superseded by [019](019-query-ir.md) — the `query` override is removed; the structured inputs stand

**Date:** 2026-09-03

## Context

Every track needed a hand-written full query. In practice most tracks are one
query shape where a list changes — a Jira project plus the set of opted-in
teams, a GitHub repo plus a label set — and the invariants that must not be
forgotten (queued status, unassigned, not blocked, not claimed, stable sort)
were re-typed into every string. Onboarding a team meant editing a JQL
expression in a PR that only its author could review. And a value spliced
into a query by hand is not validated: a team name with a quote in it is at
best a broken query.

## Decision

A track may give the parts of its query instead of the query. Jira takes
`project`, `status`, `filters` (a table of field name to allowed values, each
a `"Field" in (...)` clause), and `extra`; GitHub takes `repo` and `labels`.
`tina.query` builds the query from them, owning the universal predicates and
excluding the track's blocked and claim markers itself, and under a claim
transition offering back bot-held items still in the queued status. Every
interpolated value is validated at load — project key, field name, value
charset, repo shape, no quotes in labels — so the builders concatenate
without escaping. `query` stays the full override for anything that does not
fit; a track may not set both, and the error names the structured keys it
would have to drop.

## Consequences

- Onboarding is one array edit, reviewable by someone who is not the author.
- The universal predicates live in one place. A track that needs to relax
  them writes `query` and takes on the whole string, explicitly.
- Structured inputs are per source and the sets are disjoint; a Jira key on
  a GitHub track is a load-time error, not an ignored key.
- The builders are pure string functions imported by the config module, so
  the effective query is on the parsed track (`track.query`) — `tina
  validate`'s summary, `status`, and `dispatch` all read the same string.
