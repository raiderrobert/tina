# 19. Connectors are processes; the contract is a protocol

**Status:** Proposed

**Date:** 2026-09-08

## Context

A source adapter is Python in this repository, selected by an `if` on the
track's `source` key, with its configuration keys spelled out in Tina's own
config schema — `repo` and `labels` for GitHub, `project`, `filters`,
`claim_transition`, `blocked_transition` for Jira — and its URL shapes known
to the artifact verifier. Adding a connector means a pull request here, and a
connector that cannot be public cannot exist at all.

Three forces push against that:

- **Private connectors.** An organization running Tina has trackers Tina has
  never heard of and tracker *conventions* — workflow status names, custom
  fields, login rituals for the agent's own tools — that are theirs. Some of
  that must not live in a public repository. Today the Jira connector carries
  the shape of one deployment's Jira, generalized just enough to pass.
- **Language.** A connector author should be able to write one in TypeScript
  or Go. Any in-process plugin mechanism — entry points, `importlib`, a
  registry — fixes the language to Python and the version to Tina's, and a
  compiled host would not help: in-process loading in Go or Rust ties the
  plugin to an identical toolchain, which is a tighter coupling than "Python
  3.12", not a looser one. What makes an ecosystem polyglot is the boundary,
  not the host's language. Terraform providers are separate processes
  speaking a protocol; Terraform being written in Go is incidental.
- **Distribution.** Not every organization has a package index for private
  code. A Python plugin can be installed from a git URL, but an executable
  copied into an image needs no index, no registry, and no agreement about
  which language it was built in.

Tina already has exactly one boundary with these properties: the harness.
`[harnesses.pi] command = [...]` names a program; Tina runs it, never parses
its stdout, and reads the outcome from a file. Architecture §12 calls harness
adapters "small enough to be declarative config rather than a code plugin."
Connectors are the other adapter family, and nothing about them requires the
in-process treatment: the `Source` contract is nine coarse request/response
calls, each carrying a small JSON-shaped value, and each connector call sits
next to a network round-trip to a tracker that dwarfs any process overhead.

## Decision

A connector is an executable. Tina launches it as a subprocess and speaks to
it over standard streams in a small JSON-RPC protocol —
[docs/connector-protocol.md](../connector-protocol.md) — whose methods are the
`Source` contract as it stands: `query`, `get`, `matches`, `claim`,
`claim_prognosis`, `claimed`, `annotate`, `block`, `login`, plus an
`initialize` handshake that carries a protocol version and the track's
connector-specific options, and two optional capabilities, `build_query`
(structured inputs → a query, what `tina.query` does today) and
`artifact_endpoint` (a web URL → the API resource that proves it exists, what
`verify.api_url` does today).

Configuration names the connector the way it names the harness, and moves
every connector-specific key out of Tina's schema into a table the connector
validates:

```toml
[sources.jira]
command = ["tina-source-jira"]     # any language, installed into the image however you like

[vul]
source = "jira"
claim = "assign"                   # Tina's keys stay Tina's
[vul.options]                      # opaque to Tina; the connector validates it at initialize
project = "VUL"
claim_transition = "In Progress"
```

Every connector goes through this door, including Tina's own. The GitHub
connector ships with Tina as a console script, `tina-source-github`, and Tina
talks to it over the protocol like any other; nothing in Tina imports a
connector class at run time. The Jira connector leaves this repository to
become its own distribution, wherever its owner wants it. Per ADR-010, Tina
keeps two in-tree connectors: GitHub, and a `file` connector that reads work
items from a JSONL file and claims with a sidecar marker — the connector the
demo and the tests use, and the one a new author copies.

Conformance is a command, not a test module: `tina connector-check <command>`
drives any executable through the protocol — framing, handshake, every
method's shape, the error shape, shutdown — so a connector author's CI proves
compatibility in their language without importing anything of Tina's.

Executors are the same shape and get the same treatment later; this decision
scopes to sources, where the need is.

## Alternatives considered

**Python plugins through entry points.** A `Source` Protocol the plugin
satisfies, an `api_version` gate, `entry_points(group="tina.sources")`,
`load(name)`. The least code, and distribution works from a git URL. It fixes
connectors to Python, and to a Python and pydantic version Tina picks; a
private connector is then a private *Python package*, which is the harder of
the two things to distribute. Rejected because the language constraint is the
one being asked to go away, and because it would give Tina two adapter
mechanisms — process for the harness, import for connectors — where one
suffices.

**WASM components.** Polyglot, sandboxed, a single artifact. The component
model and its host bindings were judged too young to build a contract on, and
sandboxing is not a requirement here: a connector already holds the
tracker's credentials. Revisit when a connector author asks for it.

**Reuse the Model Context Protocol wholesale — a connector is an MCP server,
the nine methods are tools.** Considered seriously. MCP's stdio transport is
exactly the framing chosen here (newline-delimited JSON-RPC 2.0, `stderr` for
logs), it has Tier-1 SDKs in TypeScript, Python, and C# and official ones in
Go, Rust, Java, Kotlin, Swift, Ruby, and PHP, and its stdio transport carries
no authorization layer — credentials come from the environment, as today.
Against it: MCP's tools are described for language-model callers
(`tools/list` descriptions, content-block results), which a deterministic
host would use as plain typed RPC; the spec revises on a dated cadence and
its 2026-07-28 revision replaced the `initialize` handshake with a protocol
version on every request, so "MCP-compatible" is a moving target for a
contract that wants to be stable for years; and a connector author gains
little from an SDK for a protocol that is read-a-line, dispatch, write-a-line.
The framing is adopted verbatim so an MCP stdio server *could* host a
connector; the methods are Tina's. **Open for review:** if the first
connector author would rather write an MCP server, this is the decision to
reopen.

## Consequences

- A connector can be written in any language, live in any repository, and be
  installed by copying a program into the image. Nothing about it passes
  through this repository or any package index.
- Tina's config schema shrinks to Tina's own keys. `repo`, `labels`,
  `project`, `status`, `filters`, `extra`, `claim_transition`, and
  `blocked_transition` become each connector's business, validated by the
  connector at `initialize` — so `tina validate` and `tina doctor` still fail
  at load time, by asking the process instead of a pydantic model. This is a
  breaking config change; the migration is mechanical (move keys under
  `[<track>.options]`) and ships with an upgrade note.
- `tina.query` and the tracker-specific half of `tina.verify` move into the
  connectors that own that knowledge, behind `build_query` and
  `artifact_endpoint`.
- Tina owns subprocess lifecycle for one more family: spawn on first use, one
  connector process per Tina invocation, a per-request timeout, `shutdown`
  then a grace period then `kill`. A connector that dies is a `SourceError`
  with its last stderr lines attached.
- The `Source` Protocol stays, as the in-process client's shape and as the
  thing `connector-check` tests against; `sources.build()` becomes "spawn the
  configured command and return the client." The fakes in Tina's own tests are
  unchanged.
- Debugging spans two processes. In exchange, a connector's failure is a
  process exit with stderr, not a stack trace inside Tina.
- ADR-010 holds with GitHub and `file` in tree. The Jira connector's
  departure is the first proof that the door works from the outside.
- Executors remain in-process until a second decision extends this to them.
