# 2. Tina does not own scheduling

**Status:** Accepted

**Date:** 2026-07-28

## Context

A factory that polls a queue needs something to trigger the poll. The obvious move
is a `--cron` mode inside Tina, but there is no open standard for declaring a
schedule that targets native cloud schedulers, and the cron dialects are not
portable across them: EventBridge uses six fields with `?` and a year, while GCP,
Kubernetes, and Cloudflare use five-field unix cron. Any schedule Tina owned would
either be a long-lived process competing with the platform's scheduler, or a
translation layer between dialects it cannot validate.

## Decision

Cron is not a mode. An external scheduler calls `tina dispatch` on whatever
schedule it owns, in its own dialect. Cloud Scheduler, EventBridge, Kubernetes
CronJob, systemd timers, and GitHub Actions `schedule:` all work without Tina
knowing they exist.

A REST/webhook entrypoint is deferred.

## Consequences

- Tina ships no scheduler, no schedule config, and no cron parsing.
- Schedule lives in the consumer's IaC alongside everything else it deploys.
- Dispatcher invocations must stay short and bounded to clear scheduler timeouts —
  satisfied by [003](003-dispatch-worker-split.md).
- Reaction latency is bounded by poll interval. The webhook entrypoint pays off
  only when that matters more than poll latency; it needs a long-lived listener,
  auth, payload validation, and a `normalize(payload)` path per source, none of
  which v1 carries.
