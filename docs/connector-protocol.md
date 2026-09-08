# The connector protocol

**Protocol version 1 — draft, alongside [ADR-019](adr/019-connectors-are-processes.md).**

A connector is a program that gives Tina work items from a tracker and takes
lifecycle writes back. Tina runs it as a subprocess and talks to it over
standard streams. This document is the whole contract: a connector written
against it, in any language, works with any Tina that speaks version 1.

Nothing here is specific to a tracker. Everything Tina knows about Jira or
GitHub — how to build a query from a project and a team list, which URL
proves a pull request exists — lives behind this protocol, in the connector.

---

## 1. Transport

- Tina launches the connector with the `command` from its `[sources.<name>]`
  table, with Tina's environment and working directory. Credentials travel
  in the environment, the same way they reach the agent's own tools; the
  protocol carries none.
- **Messages are JSON-RPC 2.0**, UTF-8, **one per line**, and must not contain
  embedded newlines. Tina writes requests to the connector's `stdin`; the
  connector writes responses to its `stdout`. This is the framing MCP's stdio
  transport uses, so an MCP stdio server implementation can carry it.
- `stdout` is for protocol messages only. Anything else on `stdout` is a
  protocol error. `stderr` is the connector's log; Tina relays it to its own
  `stderr`, line by line, prefixed with the source name.
- Requests carry a JSON-RPC `id` and expect exactly one response. Tina sends
  one request at a time and waits; a connector need not handle concurrency.
- One connector process serves one Tina invocation (`dispatch`, `run`,
  `status`, `doctor`, `validate`). Tina spawns it on first use, sends
  `shutdown` when done, closes `stdin`, waits a grace period (5 s), then
  kills it.
- Every request has a timeout, 120 s unless `[sources.<name>] timeout` says
  otherwise. A connector that does not answer in time is a source error. A
  connector's own retry waits — a tracker's documented rate-limit backoff —
  count against it, so a connector keeps its ladder inside the timeout and
  logs each wait to `stderr`, where Tina relays it.

## 2. Handshake

The first request is always `initialize`. It carries the protocol version
Tina speaks, who Tina is, the track's connector-specific options, and the
generic lifecycle settings the connector needs to interpret them.

```jsonc
→ {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
     "protocol_version": 1,
     "client": {"name": "tina", "version": "0.2.0"},
     "track": "vul",
     "options": {"project": "VUL", "claim_transition": "In Progress"},   // [vul.options], verbatim
     "lifecycle": {"claim": "assign", "claim_label": null, "blocked_label": "tina-blocked"}
   }}
← {"jsonrpc": "2.0", "id": 1, "result": {
     "protocol_version": 1,
     "connector": {"name": "jira", "version": "1.4.0"},
     "capabilities": {"build_query": true, "verify_artifact": true}
   }}
```

- A connector that does not speak the requested `protocol_version` answers
  with error `-32001` (see §5) naming the versions it does. Tina fails the
  run at load, not later.
- **`options` is where the connector validates its configuration.** Unknown
  keys, missing required keys, and malformed values are answered with error
  `-32002` and a `fix`. This is what makes `tina validate` and `tina doctor`
  catch a typo in `[vul.options]` before any run: they initialize the
  connector and stop.
- `lifecycle` mirrors the track keys Tina owns and the connector needs:
  `claim` (`"assign"`, `"label"`, or `"none"`), `claim_label`, and
  `blocked_label`. A connector implements claiming and blocking with them.
- `capabilities` says which optional methods (§4) the connector implements.
  Tina never calls one that is not declared.
- Nothing in any message is a credential. A connector reads its tracker's
  credentials from the environment it inherited and never returns them; Tina
  never sends any. A capability that would need to hand a token across the
  pipe is designed the other way round — the connector does the call.

## 3. Methods

Every connector implements all of these. Payload shapes are in §6.

| Method | Params | Result | Used by |
|---|---|---|---|
| `query` | `{"query": str}` | `{"items": [WorkItem]}` | dispatch, status |
| `get` | `{"id": str}` | `{"item": WorkItem}` | run |
| `matches` | `{"id": str, "query": str}` | `{"matches": bool}` | run — the eligibility re-check, the whole predicate against one item |
| `claim` | `{"item": WorkItem}` | `{"claimed": bool}` | run — `true` means this worker owns the item now |
| `claim_prognosis` | `{"item": WorkItem}` | `{"would_claim": bool, "holder": str}` | run `--dry-run` — read-only; `holder` is `""` when nobody holds it |
| `claimed` | `{"query": str}` | `{"items": [WorkItem]}` | status — the query with its unclaimed clause inverted: what the bot holds |
| `annotate` | `{"item": WorkItem, "comment": str}` | `{}` | run — lifecycle write-back; the connector logs and swallows tracker failures (ADR-013) |
| `block` | `{"item": WorkItem}` | `{}` | run — apply the exclusion so the query stops matching; idempotent; best-effort like `annotate` |
| `login` | `{}` | `{"identity": str}` | doctor — one authenticated read; who the credentials act as |
| `shutdown` | `{}` | *(notification, no `id`, no response)* | every command, last |

Semantics are those of the `Source` contract in `tina/sources/base.py` and
architecture §8–§9; this table is the wire shape of the same nine calls.
Under `lifecycle.claim = "none"`, `claim` is never called and `claimed`
returns an empty list.

## 4. Optional methods

Declared in `initialize`'s `capabilities`; called only when declared.

| Method | Params | Result | Purpose |
|---|---|---|---|
| `build_query` | `{"options": {...}, "lifecycle": {...}}` | `{"query": str}` | Build the track's query from structured inputs when the track sets no `query`. The connector owns the universal predicates — queued, unassigned, not blocked, not claimed, stable order — for its tracker. |
| `verify_artifact` | `{"url": str}` | `{"exists": bool}` or `null` | For artifact verification: whether the artifact behind a web URL exists, checked by the connector with its own credentials — a private repository's pull request is a 404 to an anonymous `GET`. `null` means "not mine"; Tina asks every configured connector and falls back to fetching the URL as given, anonymously. Credentials never cross the pipe. |

## 5. Errors

Failures are JSON-RPC error objects. `message` says what broke and names the
tracker path involved; `data.fix`, when present, is the single action that
resolves it. Tina renders both.

| Code | Meaning | Tina's reaction |
|---|---|---|
| `-32001` | unsupported protocol version; `data.supported` lists the connector's | fail at load |
| `-32002` | invalid options; `data.fix` names the key | fail at load (`validate`, `doctor`, `dispatch`, `run`) |
| `-32003` | the tracker refused or could not be reached | `SourceError` — the command exits 1 with the message |
| `-32004` | no such item | `SourceError` |
| `-32600` … `-32603` | JSON-RPC's own: malformed request, unknown method, invalid params, internal | protocol error; the connector is at fault |

`annotate` and `block` do not raise on tracker failure: they are best-effort
by contract, so the connector logs the failure to `stderr` and returns `{}`.

A connector that exits, closes `stdout`, or writes a non-JSON line has broken
the protocol; Tina reports a `SourceError` with the connector's last `stderr`
lines attached.

## 6. Payloads

```jsonc
WorkItem = {
  "id": str,                 // the tracker's identifier: "VUL-1", "owner/name#42"
  "source": str,             // the connector's name
  "title": str,
  "description": str,        // plain text; the connector flattens rich formats
  "url": str | null,         // where a person would look at it
  "raw": object              // the untouched tracker payload — the escape hatch
}
```

Field names and types are exactly those of `tina.models.WorkItem`, so the
JSON block the agent sees in its prompt is what the connector sent.

## 7. What Tina promises a connector

- `initialize` first, `shutdown` last, one request in flight at a time.
- The environment and working directory Tina itself has.
- `options` exactly as written in the track table, undeclared and unchanged.
- No writes will be requested on a `--dry-run`, `status`, `validate`, or
  `doctor` invocation: only `initialize`, `login`, `query`, `get`, `matches`,
  `claim_prognosis`, `claimed`, `build_query`, `verify_artifact`, and
  `shutdown`. A connector may enforce that.

## 8. Conformance

`tina connector-check <command>` drives a connector through this document:
the handshake with a supported and an unsupported version, an `initialize`
with bad options, every method's response shape, the error shape, `stderr`
relaying, and `shutdown`. It needs the connector's own environment
(credentials) and is read-only unless given `--write-to <item-id>`, in which
case it also exercises `claim`, `annotate`, and `block` against that one item.
Run it in the connector's CI; it is the only thing of Tina's a connector needs
to be tested against.

## 9. Configuration reference

```toml
[sources.<name>]
command = ["…"]      # required: the program and its arguments
timeout = 120        # seconds per request; optional

[<track>]
source = "<name>"    # selects [sources.<name>]
query = "…"          # optional when the connector offers build_query
[<track>.options]    # optional; opaque to Tina, validated by the connector
```

The generic track keys — `mode`, `source`, `query`, `track`, `enabled`,
`model`, `result`, `claim`, `claim_label`, `blocked_label`, `on_failure`,
`max_concurrency`, `env` — remain Tina's and are documented by
`tina config-options`.

## 10. Versioning

`protocol_version` is an integer. It increments only for an incompatible
change to this document: a method removed or its shape changed. Adding an
optional method or an optional field is not a version change — a connector
that does not know a field ignores it, and Tina calls no optional method a
connector did not declare. A Tina release states which versions it speaks; a
connector that speaks one of them works with it.
