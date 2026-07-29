# 8. Tracks are installed via napoln; Tina ships none

**Status:** Accepted

**Date:** 2026-07-28

## Context

A track's skill is the agent work — the variable part of every track and the
part each company will write for itself. If Tina shipped tracks it would also
need to fetch, version, pin, and upgrade them, which is a package manager.
Tracks are skills, and [napoln](https://github.com/raiderrobert/napoln)
is already a package manager for skills.

## Decision

Tina ships no tracks and **contains no fetching code**. The consumer's image
build runs `napoln install`, skills land on disk, and Tina reads them from a
directory. Versioning, pinning, and three-way-merge upgrades are napoln's job.

Distribution is three artifacts: the OSS core (this repo, no tracks), public
reference tracks that are copyable, and a per-company private consumer repo
that pulls both and deploys via its own IaC.

## Consequences

- Tina has no track registry, no version resolution, and no upgrade path to
  maintain.
- Tracks can be private without Tina knowing anything about private
  distribution.
- The track directory is the interface. Tina resolves a track's `track` key
  (defaulting to the table key) to a skill in that directory and does nothing
  else.
- A missing or misnamed skill is a config error surfaced at run time, not an
  install-time failure Tina can catch.
- Reference tracks are copyable rather than depended on, so a company can fork
  one without pinning to its upstream.
