# The control file

Runtime policy — pause and throttle — lives in a small TOML file, separate
from `tina.toml`:

```toml
paused = false
max_concurrency = 5
```

`tina dispatch` reads it fresh on every cycle and exits 0 when paused. The
effective worker count is `min(--limit, max_concurrency)`: the file can lower
the caller's ceiling, never raise it. Values above the in-code ceiling are
clamped. Workers never read the file — an item already claimed is worth more
finished than stopped.

Tina finds the file in this order
([ADR-012](adr/012-control-file-is-a-path.md)):

1. `TINA_CONTROL_INLINE` — raw TOML in the environment.
2. `TINA_CONTROL` — a path in the environment.
3. `control = "/mnt/config/control.toml"` — a top-level key in `tina.toml`.
4. Nothing configured — defaults: not paused, no throttle.

## Read every dispatch, no cache

The file is a kill switch. Staleness is the failure mode, so Tina never caches
it. Edit the object behind the mount and the next dispatch cycle obeys it.

## Mounting it, per platform

Tina reads a path; the deployment decides how the bytes get there. Every
platform has a way to make a remotely editable object look like a file that
refreshes:

| Platform | Mechanism |
|---|---|
| Cloud Run | GCS volume mount: mount a bucket, set `TINA_CONTROL=/mnt/config/control.toml`, edit the object in the bucket |
| ECS / Fargate | EFS mount, or S3 Mountpoint |
| Kubernetes | ConfigMap volume: `kubectl edit configmap tina-control` and the kubelet refreshes the file |
| Lambda | EFS |
| Nomad / systemd / bare metal | bind mount, or a file the operator edits in place |
| GitHub Actions or other CI-as-executor | `TINA_CONTROL_INLINE` holding the TOML — mounting is awkward on a runner |
| Local dev | it is just a file: `TINA_CONTROL=./control.toml` |

## What a broken mount does

The loader fails closed ([ADR-011](adr/011-control-plane-data-plane-split.md)
I2). Operators can predict the outcome from this table:

| Condition | Result | Why |
|---|---|---|
| File absent | defaults | A first deploy must not brick the factory |
| Malformed TOML, or invalid values | paused | A typo'd emergency edit must not unleash it |
| Permission denied | paused | A broken mount must not look like "no control plane configured" |
| Any other read error | paused | Same reasoning |

Every load logs the source consulted and the effective values on stdout, so a
surprising pause or throttle is diagnosable from the dispatch log alone.

## Security

Write access to the control file is equivalent to "stop the factory" or
"raise concurrency to the ceiling." Give the path the same ACLs as the deploy
path — never a world-writable bucket. Tina clamps `max_concurrency` in code,
but the clamp is a backstop, not a substitute for access control.
