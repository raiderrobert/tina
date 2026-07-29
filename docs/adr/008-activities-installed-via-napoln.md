# 8. Activities are installed via napoln; Tina ships none

**Status:** Accepted

**Date:** 2026-07-28

## Context

An activity is the agent work, expressed as a skill — the variable part of every
workflow and the part each company will write for itself. If Tina shipped
activities it would also need to fetch, version, pin, and upgrade them, which is a
package manager. Activities are skills, and [napoln](https://github.com/raiderrobert/napoln)
is already a package manager for skills.

## Decision

Tina ships no activities and **contains no fetching code**. The consumer's image
build runs `napoln install`, skills land on disk, and Tina reads them from a
directory. Versioning, pinning, and three-way-merge upgrades are napoln's job.

Distribution is three artifacts: the OSS core (this repo, no activities), public
reference activities that are copyable, and a per-company private consumer repo
that pulls both and deploys via its own IaC.

## Consequences

- Tina has no activity registry, no version resolution, and no upgrade path to
  maintain.
- Activities can be private without Tina knowing anything about private
  distribution.
- The activity directory is the interface. Tina resolves a workflow's `activity`
  key (defaulting to the workflow key) to a skill in that directory and does
  nothing else.
- A missing or misnamed skill is a config error surfaced at run time, not an
  install-time failure Tina can catch.
- Reference activities are copyable rather than depended on, so a company can fork
  one without pinning to its upstream.
