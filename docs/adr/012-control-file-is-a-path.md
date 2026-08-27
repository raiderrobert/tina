# 12. The control file is a path, not a fetched object

**Status:** Proposed

**Date:** 2026-08-27

## Context

The control file carries runtime policy
([ADR-011](011-control-plane-data-plane-split.md)): `paused` and
`max_concurrency`. Its bytes must reach the dispatcher, and something has to
decide how. [ADR-008](008-tracks-installed-via-napoln.md) already answered
this question for tracks: Tina contains no fetching code; the consumer puts
skills on disk; Tina reads a directory. A second, contradictory precedent
would be worse than either choice alone.

The requirement is small: a few hundred bytes, read once per dispatch, no
write, no list, no watch. That does not justify an SDK dependency. A plain
path covers every target platform with zero code in Tina, because every
platform can make a remotely editable object look like a file that refreshes:
GCS volume mounts on Cloud Run, EFS or S3 Mountpoint on ECS, ConfigMap volumes
on Kubernetes, EFS on Lambda, bind mounts elsewhere, a literal file locally.

## Decision

The control file is a path Tina reads. The deployment decides how the bytes
get there.

```toml
control = "/mnt/config/control.toml"   # optional; absent → defaults
```

`TINA_CONTROL` overrides the config key, so one image works across
environments. `TINA_CONTROL_INLINE` holds raw TOML for runners where mounting
is awkward — CI-as-executor — and is the native answer for ConfigMap-to-env.

The dispatcher reads the file on every dispatch, with no cache. It is a kill
switch, and staleness is the failure mode a cache would introduce.

**Rejected: watch semantics.** A watch needs a long-lived listener, which
[ADR-003](003-dispatch-worker-split.md) already ruled out for the dispatcher.

**Rejected: an object-store client, or calling the mechanism "S3-like".** The
name implies an object store is required, and reads as AWS-flavored to
precisely the users this project should not alienate.

**Deferred, not rejected: remote fetch.** If a genuine need appears, add a URI
scheme registry — `file` and `https` built in, `s3`/`gs`/`az` as optional
extras mirroring how `cloudrun` is packaged. Never an SDK in core.

## Consequences

- Tina stays free of cloud SDKs and network configuration code.
- Every platform gets a short mount recipe instead of a client integration.
- The loader is a file read plus validation, testable without any cloud.
- Write access to the control file is write access to the factory's throttle
  and kill switch. The path must carry deploy-grade ACLs; the docs say so.
- A broken mount surfaces as a read error, which the loader must treat as
  "pause", never as "no control plane configured"
  ([ADR-011](011-control-plane-data-plane-split.md) I2).
