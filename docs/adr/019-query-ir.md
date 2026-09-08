# 19. The query is a predicate tree; sources compile it and declare what they compile

**Status:** Proposed

**Date:** 2026-09-08

## Context

ADR-018 let a track give the parts of its query and kept `query` as the raw
override. Both paths produced a string, and the string was what the adapters
received. Tina then had to do two things to that string it did not write:
invert the exclusion for `status` (`assignee IS EMPTY` to the bot,
`no:assignee` to `assignee:<login>`, `-label:x` to `label:x`) and scope it to
one item for the worker's eligibility re-check. Each adapter grew its own
scanner for this — two regexes in Jira, a whitespace tokenizer and a
qualifier evaluator in GitHub — and each carried a documented limitation: a
quoted literal containing the phrase, `IS NOT EMPTY` versus `IS EMPTY`, a
comma inside a label name, GitHub qualifiers the re-check did not understand
and so passed. The raw override was the reason the scanners had to exist: a
string Tina did not build has no structure Tina can rely on.

## Decision

A track's query is a `tina.query.Query`: a small frozen tree — scope,
assignee predicate, status, required labels, excluded labels, field filters,
opaque `extra` text, optional item. `tina.query.build` produces it from the
config parts and owns the universal predicates. The two operations Tina
performs are tree operations: `held_by` flips the exclusion node, `scoped_to`
sets the item node.

Each source module exposes `compile(q) -> str` for its own syntax, and GitHub
additionally `satisfies(issue, q)` for the re-check its search cannot
express. The `Source` protocol takes a `Query`, not a string. Each source
declares the optional nodes it compiles in `tina.query.SOURCE_FEATURES`; a
track using a node its source lacks fails at config load naming the key, and
a compiler handed one raises rather than dropping it.

The raw `query` key is removed. `extra` is the only place native text
enters, and it is appended verbatim and never read — so nothing Tina has to
rewrite can hide in it.

## Consequences

- The scanners and their limitations are gone. `claimed` and `matches` are
  one line each. GitHub's re-check evaluates every node; there is no
  unstructured remainder to pass.
- `claimed` needs no identity lookup: `currentUser()` and `assignee:@me` name
  the credential holder in both query languages.
- Config break: a track with a hand-written `query` must be re-expressed. On
  Jira nothing is lost — anything not universal goes in `extra`. On GitHub
  anything beyond `repo` and `labels` (a milestone, an author, a comma-list
  any-of label) is not expressible until it is added as a named feature.
  GitHub gets no `extra` because `matches` cannot evaluate opaque search
  text locally, and a re-check that waves text through is what this
  replaces.
- Adding a source is `compile`, optionally `satisfies`, a feature set, and
  the claim methods. Adding a feature to a source is extending `compile` and
  its set; the config surface does not change.
- `labels` becomes a feature both sources compile: on Jira, one `labels =
  "x"` clause per label. Previously a GitHub-only key.
- The effective query is no longer a string on the parsed track.
  `TrackConfig.query` is the tree; `tina.sources.render(track)` produces the
  native string `tina validate` prints.
- Supersedes the second clause of ADR-018, "`query` remains the override".
  The first clause — parts build the query — stands.
