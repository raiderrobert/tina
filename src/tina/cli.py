"""`tina dispatch`, `tina run`, `tina status`, and the admission and
introspection commands around them. Two roles, one image.

The typer commands are a thin shell: they parse argv, load config, and turn a
`TinaError` into exit 1. The orchestration lives in `dispatch_track`,
`run_item`, and `status_track`, which take already-built objects so callers
(and tests) can inject a source, an executor, or a governor.
"""

from __future__ import annotations

import os
import shlex
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

import typer

from tina import (
    control,
    doctor,
    executors,
    harness,
    introspect,
    log,
    output,
    prompt,
    sources,
    verify,
)
from tina import validate as admission
from tina.config import Config, ConfigError, TrackConfig
from tina.config import load as load_config
from tina.errors import TinaError
from tina.executors.base import Executor
from tina.governor import Governor
from tina.models import SWEEP_ITEM, OutcomeReport, OutcomeStatus, RunRecord, WorkItem
from tina.sources.base import Source

#: Where the config is when no `--config` is given: `TINA_CONFIG`, else
#: `tina.toml` in the working directory. One image, mounted anywhere.
CONFIG_VAR = "TINA_CONFIG"
DEFAULT_CONFIG = Path("tina.toml")

#: Live workers a queue track may have when nothing — `--limit`, the track,
#: the control file — says otherwise. Conservative on purpose.
DEFAULT_LIMIT = 1

logger = log.get_logger("tina")

app = typer.Typer(
    name="tina",
    help="An autonomous factory: claim a work item, run an agent once, record it.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)

TrackOption = Annotated[str, typer.Option("--track", help="Track table in the config.")]
ConfigOption = Annotated[
    Path | None,
    typer.Option(
        "--config", help=f"Path to the TOML config file. Default: ${CONFIG_VAR}, else tina.toml."
    ),
]


def _config_path(given: Path | None) -> Path:
    if given is not None:
        return given
    return Path(os.environ.get(CONFIG_VAR) or DEFAULT_CONFIG)


def _version_callback(value: bool) -> None:
    """Print the version and stop before any config is loaded.

    The module is imported and read through, rather than binding `__version__`
    at import time, so the value stays correct when tests reload `tina`.
    """
    if value:
        import tina

        typer.echo(f"tina {tina.__version__}")
        raise typer.Exit()


@app.callback()
def _global_options(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version_callback, is_eager=True, help="Show tina version."
        ),
    ] = False,
) -> None:
    """An autonomous factory: claim a work item, run an agent once, record it."""


@app.command()
def dispatch(
    track: TrackOption,
    limit: Annotated[
        int | None,
        typer.Option(
            "--limit",
            help="Cap on live workers. Lowers the track's or the control file's cap; "
            f"with none of the three set, {DEFAULT_LIMIT}.",
        ),
    ] = None,
    config: ConfigOption = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview the matched items without enqueueing anything."),
    ] = False,
) -> None:
    """Run the source query and enqueue workers up to the effective cap."""
    with _exit_on_tina_error("dispatch"):
        dispatch_track(load_config(_config_path(config)), track, limit, dry_run=dry_run)


@app.command()
def run(
    track: TrackOption,
    item: Annotated[
        str | None,
        typer.Option("--item", help="Tracker identifier of the work item. Sweep tracks take none."),
    ] = None,
    config: ConfigOption = None,
    model: Annotated[
        str | None,
        typer.Option(
            "--model",
            help="Run on this model instead of the track's own, for trying one out.",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Preview the run without claiming the item or running the agent.",
        ),
    ] = False,
) -> None:
    """Claim one work item, run the agent, record the outcome."""
    with _exit_on_tina_error("run"):
        run_item(load_config(_config_path(config)), track, item, dry_run=dry_run, model=model)


@app.command()
def status(
    track: TrackOption,
    config: ConfigOption = None,
) -> None:
    """Report how many items are waiting and how many workers hold."""
    with _exit_on_tina_error("status"):
        status_track(load_config(_config_path(config)), track)


@app.command()
def tracks(
    config: ConfigOption = None,
    format: Annotated[
        Literal["text", "json"],
        typer.Option("--format", help="text: one name per line; json: one object per track."),
    ] = "text",
) -> None:
    """List the configured tracks, so infrastructure can be derived from the same file."""
    with _exit_on_tina_error("tracks"):
        loaded = load_config(_config_path(config))
        text = (
            introspect.tracks_json(loaded) if format == "json" else introspect.tracks_text(loaded)
        )
        typer.echo(text, nl=False)


@app.command(name="config-options")
def config_options(
    format: Annotated[
        Literal["text", "markdown", "json"],
        typer.Option("--format", help="text, markdown (a table), or json (JSON Schema)."),
    ] = "text",
) -> None:
    """Explain every track key: type, default, and meaning, from the schema itself."""
    renderers = {
        "text": introspect.options_text,
        "markdown": introspect.options_markdown,
        "json": introspect.options_json,
    }
    typer.echo(renderers[format](), nl=False)


@app.command()
def validate(
    config: ConfigOption = None,
    track: Annotated[
        str | None,
        typer.Option("--track", help="Scope the skill checks to one track."),
    ] = None,
) -> None:
    """Check the config and every track skill statically. Exit 1 on any error."""
    log.configure()
    report = admission.validate(_config_path(config), only=track)
    for error in report.errors:
        output.error(error)
    for line in report.summary:
        typer.echo(line, err=True)
    for warning in report.warnings:
        typer.echo(typer.style("warning: ", fg=output.DRY_RUN) + warning, err=True)
    logger.info(
        "validated",
        extra={"errors": len(report.errors), "warnings": len(report.warnings)},
    )
    if not report.ok:
        raise typer.Exit(code=1)


@app.command(name="doctor")
def doctor_command(
    config: ConfigOption = None,
    track: Annotated[
        str | None,
        typer.Option("--track", help="Probe one track's source instead of all of them."),
    ] = None,
) -> None:
    """Probe the deployment: credentials, queries, harness, executor, control. Read-only."""
    log.configure()
    checks = doctor.diagnose(_config_path(config), only=track)
    for check in checks:
        output.check(check.name, check.ok, check.detail)
        logger.info("doctor", extra={"check": check.name, "ok": check.ok, "detail": check.detail})
    if not all(check.ok for check in checks):
        raise typer.Exit(code=1)


@contextmanager
def _exit_on_tina_error(command: str) -> Iterator[None]:
    """Exit 1 for Tina's own failures — and only those.

    An agent reporting `failed` never lands here: that is an outcome, not a
    process failure, so the run still exits 0.

    Both halves of the boundary fire: the JSON record on stdout for whatever is
    collecting runs, and the human block on stderr for whoever ran the command.
    """
    log.configure()
    try:
        yield
    except TinaError as exc:
        logger.error(str(exc), extra={"command": command})
        output.error(str(exc), exc.cause, exc.fix)
        raise typer.Exit(code=1) from None


def dispatch_track(
    config: Config,
    track_name: str,
    limit: int | None = None,
    source: Source | None = None,
    executor: Executor | None = None,
    dry_run: bool = False,
    governor: Governor | None = None,
) -> None:
    """Query, take up to the effective limit, enqueue one worker each.

    The dispatcher never runs an agent and never claims — workers claim, so a
    dispatcher that dies mid-loop leaves nothing stuck.

    Policy is read here and nowhere else (ADR-011 I1): the worker never sees
    the control file, so an in-flight run completes with the control plane
    unavailable. Paused exits before the source is even built — a kill switch
    must not depend on tracker credentials.

    `dry_run` moves the boundary to the last step only: the real source is built
    and the real query runs against the live tracker, but no executor is ever
    constructed. That a preview enqueues nothing follows from the absence of an
    executor, not from a branch inside the loop that nobody took. The one thing
    it therefore cannot report is a `cloudrun` executor with no
    `[executors.cloudrun]` table — a table that is present but incomplete still
    fails at config load, whichever mode this runs in. Having no executor, it
    also cannot count in-flight workers, so it assumes zero and says so.

    The budget is `max(0, ceiling − in flight)` (ADR-016), where the ceiling
    is the lowest of `--limit`, the track's own `max_concurrency`, and the
    control file's — except that a track declaring its own cap opted out of
    the control file's knob, so only `--limit` lowers it further. The ceiling
    caps live workers, not launches per call, or a 15-minute scheduler
    multiplies the knob by every hour a worker runs. Items already in flight
    are skipped rather than re-enqueued — the dedupe `claim = "none"` needs.

    A `governor`, when the deployment supplies one, may lower the ceiling
    further for this cycle and is told what the cycle did afterwards. Tina
    ships none; a governor that raises is treated as absent for the cycle.
    """
    track = config.track(track_name)
    _require_enabled(config, track)
    policy = control.load(config.control_path())
    if policy.paused:
        _paused_dispatch(track, policy, dry_run)
        return

    if track.mode == "sweep":
        _dispatch_sweep(config, track, policy, executor, dry_run)
        return

    effective, limit_origin = _effective_limit(limit, track, policy)
    source = source or sources.build(track)
    if dry_run:
        items = source.query(track.query)[: max(effective, 0)]
        _preview(config, track, items, limit, effective, limit_origin, policy)
        return

    executor = executor or executors.build(config)
    in_flight = executor.running(track.name)
    cap, cap_origin = _governed(governor, track.name, effective, len(in_flight), limit_origin)
    budget = max(0, cap - len(in_flight))
    items = source.query(track.query)
    logger.info(
        "dispatching",
        extra={
            "track": track.name,
            "limit": limit,
            "effective_limit": cap,
            "limit_origin": cap_origin,
            "ceiling": effective,
            "in_flight": len(in_flight),
            "budget": budget,
            "matched": len(items),
        },
    )
    running = set(in_flight)
    launched = 0
    for item in items:
        if launched >= budget:
            break
        if item.id in running:
            logger.info("already in flight", extra=_item_fields(track.name, item, config.executor))
            continue
        executor.enqueue(track.name, item.id)
        logger.info("enqueued", extra=_item_fields(track.name, item, config.executor))
        launched += 1
    _report_to_governor(
        governor,
        track.name,
        ceiling=effective,
        cap=cap,
        in_flight=len(in_flight),
        budget=budget,
        matched=len(items),
        launched=launched,
    )


def _governed(
    governor: Governor | None, track: str, ceiling: int, in_flight: int, origin: str
) -> tuple[int, str]:
    """The governor's cap for the cycle, clamped to the ceiling, or the ceiling."""
    if governor is None:
        return ceiling, origin
    try:
        cap = governor.cap(track, ceiling, in_flight)
    except Exception as exc:
        logger.warning(
            "governor failed; using the ceiling", extra={"track": track, "error": str(exc)}
        )
        return ceiling, origin
    if cap is None or cap >= ceiling:
        return ceiling, origin
    return max(cap, 0), "governor"


def _report_to_governor(governor: Governor | None, track: str, **facts: int) -> None:
    if governor is None:
        return
    try:
        governor.record(track, **facts)
    except Exception as exc:
        logger.warning("governor record failed", extra={"track": track, "error": str(exc)})


def _require_enabled(config: Config, track: TrackConfig) -> None:
    """A disabled track refuses loudly, in every mode.

    A silent no-op would look identical to an empty backlog, which is the
    wrong thing to be ambiguous about.
    """
    if not track.enabled:
        raise ConfigError(
            f"{config.path}: track {track.name!r} is disabled (enabled = false)",
            fix=f"Set enabled = true in [{track.name}], or drop the key.",
        )


def _effective_limit(
    limit: int | None, track: TrackConfig, policy: control.LoadedPolicy
) -> tuple[int, str]:
    """The cap on live workers and which knob set it.

    A track's own `max_concurrency` wins over the control file's: the file is
    the fleet knob, and a track that declares its own throughput opted out of
    it (`paused` still stops it). `--limit` can only lower whichever applies.
    With nothing set anywhere, `DEFAULT_LIMIT`. The origin names which bound
    won, so a cycle that launched fewer workers than expected is explainable
    from the dispatch record alone.
    """
    if track.max_concurrency is not None:
        ceiling, origin = track.max_concurrency, "track max_concurrency"
    elif policy.max_concurrency is not None:
        ceiling, origin = policy.max_concurrency, "max_concurrency"
    else:
        ceiling, origin = None, "default"
    if limit is not None and (ceiling is None or limit < ceiling):
        return limit, "--limit"
    if ceiling is None:
        return DEFAULT_LIMIT, origin
    return ceiling, origin


def _paused_dispatch(track: TrackConfig, policy: control.LoadedPolicy, dry_run: bool) -> None:
    """Exit 0 before any source query. A paused factory is working as intended.

    The message is `would pause`, never `dispatch paused`, when previewing —
    the same message discipline the other previews keep. A preview that
    ignored the kill switch would mislead in the one situation a preview
    matters most.
    """
    if not dry_run:
        logger.info("dispatch paused", extra={"track": track.name, "control_origin": policy.origin})
        return
    logger.info(
        "would pause",
        extra={"track": track.name, "control_origin": policy.origin, "dry_run": True},
    )
    output.dry_run_header()
    output.would(f"Would exit paused — control policy from {policy.origin}; no query would run")
    output.dry_run_footer(action="exit paused without querying")


def _dispatch_sweep(
    config: Config,
    track: TrackConfig,
    policy: control.LoadedPolicy,
    executor: Executor | None,
    dry_run: bool,
) -> None:
    """Enqueue exactly one worker with no item. No source, no query.

    `--limit` does not apply: a sweep has no queue to take more of, and the
    skill discovers its own work. The control file still gates it — a sweep
    launch counts as one worker, so `max_concurrency = 0` launches nothing —
    and so does a sweep worker still in flight, or an hour-long sweep on a
    15-minute scheduler stacks four deep (ADR-016).
    """
    if policy.max_concurrency is not None and policy.max_concurrency < 1:
        workers, limit_origin = 0, "max_concurrency"
    else:
        workers, limit_origin = 1, "sweep"
    if dry_run:
        fields = _sweep_dispatch_fields(track.name, workers, limit_origin, in_flight=0)
        logger.info("dispatching", extra=fields | {"dry_run": True})
        _preview_sweep(config, track, workers, policy)
        return

    executor = executor or executors.build(config)
    in_flight = executor.running(track.name)
    if workers and in_flight:
        workers, limit_origin = 0, "in_flight"
    logger.info(
        "dispatching",
        extra=_sweep_dispatch_fields(track.name, workers, limit_origin, len(in_flight)),
    )
    if workers:
        executor.enqueue(track.name)
        logger.info("enqueued", extra=_sweep_fields(track.name, config.executor))
    elif in_flight:
        logger.info("already in flight", extra=_sweep_fields(track.name, config.executor))


def _sweep_dispatch_fields(
    track: str, workers: int, limit_origin: str, in_flight: int
) -> dict[str, Any]:
    """The sweep `dispatching` record, one shape across real and dry runs."""
    return {
        "track": track,
        "mode": "sweep",
        "workers": workers,
        "limit_origin": limit_origin,
        "in_flight": in_flight,
    }


def _preview_in_flight(config: Config) -> None:
    """Admit the zero a preview assumes, when it is an assumption at all.

    A dry run builds no executor, so it cannot count in-flight workers.
    Local workers finish inside `enqueue`, making zero exact and the line
    noise; any other executor would have been asked over an API.
    """
    if config.executor != "local":
        output.would(
            f"Would assume 0 workers in flight — counting them would call the {config.executor} API"
        )


def _preview_sweep(
    config: Config, track: TrackConfig, workers: int, policy: control.LoadedPolicy
) -> None:
    """The dry-run half of a sweep dispatch: same verdict, no executor."""
    output.dry_run_header()
    if policy.origin != "defaults":
        throttle = "unset" if policy.max_concurrency is None else policy.max_concurrency
        output.would(
            f"Would apply control policy from {policy.origin}: max_concurrency {throttle},"
            f" sweep workers {workers}"
        )
    _preview_in_flight(config)
    if workers:
        output.would(f"Would enqueue one sweep worker via {config.executor} — no query would run")
        logger.info(
            "would enqueue",
            extra=_sweep_fields(track.name, config.executor) | {"dry_run": True},
        )
    output.dry_run_footer()


def _sweep_fields(track: str, executor: str) -> dict[str, str]:
    """The per-item stdout schema, with the stable sweep marker as the item."""
    return {"track": track, "item": SWEEP_ITEM, "url": "", "executor": executor}


def _item_fields(track: str, item: WorkItem, executor: str) -> dict[str, str]:
    """The per-item stdout schema, shared by `enqueued` and `would enqueue`.

    One function so the two lines cannot drift: anything parsing the log by
    field keeps working across both modes.
    """
    return {
        "track": track,
        "item": item.id,
        "url": str(item.url or ""),
        "executor": executor,
    }


def _preview(
    config: Config,
    track: TrackConfig,
    items: list[WorkItem],
    limit: int | None,
    effective: int,
    limit_origin: str,
    policy: control.LoadedPolicy,
) -> None:
    """The dry-run half: same query, same fields, no executor and no enqueue.

    The message is `would enqueue`, never `enqueued`, so a collector filtering
    on `message` can never count a preview as a real dispatch. The `dry_run`
    marker is added only here, so a normal dispatch carries no such key at all.

    The policy line appears only when a control plane is configured: with
    defaults there is no policy to report, and the preview stays byte-for-byte
    what it was before the control plane existed. The in-flight line follows
    the same discipline: it appears only when the assumption could be wrong —
    counting non-local workers is an API call a dry run never makes, whereas
    local workers are synchronous and zero is exact.
    """
    logger.info(
        "dispatching",
        extra={
            "track": track.name,
            "limit": limit,
            "effective_limit": effective,
            "limit_origin": limit_origin,
            "in_flight": 0,
            "budget": max(effective, 0),
            "matched": len(items),
            "dry_run": True,
        },
    )
    output.dry_run_header()
    if policy.origin != "defaults":
        throttle = "unset" if policy.max_concurrency is None else policy.max_concurrency
        output.would(
            f"Would apply control policy from {policy.origin}: max_concurrency {throttle},"
            f" effective limit {effective} (from {limit_origin})"
        )
    _preview_in_flight(config)
    for item in items:
        line = f"Would enqueue {item.id} via {config.executor}"
        output.would(f"{line} — {item.title}" if item.title else line)
        logger.info(
            "would enqueue",
            extra=_item_fields(track.name, item, config.executor) | {"dry_run": True},
        )
    output.dry_run_footer(f"{len(items)} items matched (limit {effective}).")


def status_track(config: Config, track_name: str, source: Source | None = None) -> None:
    """Two counts off two tracker queries. Reads no local state and writes nothing.

    The counts are two halves of one question: the same configured query, once
    as `dispatch` runs it and once with its unclaimed clause inverted by the
    adapter. Nothing is claimed and no executor is ever constructed — which is
    the guarantee, since there is no call here that could do either.
    """
    track = config.track(track_name)
    if track.mode == "sweep":
        raise ConfigError(
            f"{config.path}: track {track.name!r} is a sweep track — there is no queue to count",
            fix="Status reads the source query; sweep tracks have none.",
        )
    source = source or sources.build(track)

    unclaimed = len(source.query(track.query))
    in_flight = len(source.claimed(track.query))

    logger.info(
        "status",
        extra={"track": track.name, "matched": unclaimed, "in_flight": in_flight},
    )
    output.counts(f"Track {track.name}", {"unclaimed": unclaimed, "in flight": in_flight})


def run_item(
    config: Config,
    track_name: str,
    item_id: str | None,
    source: Source | None = None,
    executor: Executor | None = None,
    dry_run: bool = False,
    model: str | None = None,
) -> RunRecord | None:
    """Claim one item, run the agent once, verify, record.

    `item_id` is required exactly when the track has a queue: a sweep track
    takes none and refuses one, since there is no source to fetch it from.

    `model` runs this one execution on a model other than the track's own —
    for trying a model out without editing the config. It is subject to the
    same rule as the track's: the harness command must reference `{model}`.

    Returns the record it logged. Every agent outcome is a successful run — the
    outcome is data, not a process failure — so this never signals via an
    exception unless Tina itself broke.

    The executor is built only to ask `run_url()` — the worker's own log link,
    which the record carries for every outcome. Nothing is enqueued here, and
    construction is deliberately cheap: no client exists until an enqueue.

    `dry_run` returns `None`, because a preview produced no run and there is no
    record to hand back. The prefix up to the claim is executed for real, so
    the preview's fidelity comes from doing the read-only work rather than
    from describing it.
    """
    started = time.monotonic()
    track = _with_model(config, config.track(track_name), model)
    _require_enabled(config, track)

    if track.mode == "sweep":
        if item_id is not None:
            raise ConfigError(
                f"{config.path}: track {track.name!r} is a sweep track and takes no --item",
                fix="Drop --item; a sweep run has no work item.",
            )
        return _run_sweep(config, track, executor, dry_run, started)
    if item_id is None:
        raise ConfigError(
            f"{config.path}: track {track.name!r} runs from a queue and needs --item",
            fix='Pass --item <id>, or set mode = "sweep" on the track.',
        )

    source = source or sources.build(track)

    item = source.get(item_id)
    if dry_run:
        _preview_run(config, track, source, item, started)
        return None

    executor = executor or executors.build(config)
    run_url = executor.run_url()

    # The eligibility re-check (ADR-014): between dispatch and worker start the
    # item can be assigned, closed, labeled, or worked by a human. Before any
    # write, for every track — under claim = "none" it is the only guard.
    if not source.matches(item.id, track.query):
        logger.info("no longer matches", extra={"track": track.name, "item": item.id})
        return _record(
            track.name,
            item.id,
            OutcomeReport(
                outcome=OutcomeStatus.NO_ACTION_NEEDED,
                details="the item no longer matches the track query",
            ),
            exit_code=None,
            started=started,
            run_url=run_url,
        )

    if track.claim != "none" and not source.claim(item):
        logger.info("already claimed", extra={"track": track.name, "item": item.id})
        return _record(
            track.name,
            item.id,
            OutcomeReport(
                outcome=OutcomeStatus.NO_ACTION_NEEDED,
                details="another worker holds this item",
            ),
            exit_code=None,
            started=started,
            run_url=run_url,
        )

    harness_config = config.harness_config()
    with tempfile.TemporaryDirectory(prefix="tina-") as tmp:
        workdir = Path(tmp)
        text = prompt.build(config.track_dir(track), item, harness.outcome_path(workdir))
        result = harness.run(harness_config, text, workdir, model=track.model, env=track.env)
        harness.capture(result.session_dir, config.artifacts_path(), item.id)

    report = verify.verify(result.report)
    record = _record(track.name, item.id, report, result.exit_code, started, run_url)
    _write_back(track, source, item, record)
    return record


def _with_model(config: Config, track: TrackConfig, model: str | None) -> TrackConfig:
    """The track with a one-run model override applied, validated like the
    track's own: whitespace is rejected, and the harness must reference
    `{model}` or the override would silently never reach it."""
    if model is None:
        return track
    if not model.strip() or any(c.isspace() for c in model):
        raise ConfigError(f"--model {model!r} must be non-empty and contain no whitespace")
    if not config.harness_config().command.uses("model"):
        raise ConfigError(
            f"{config.path}: --model given but harness {config.harness!r} never references"
            " {model}",
            fix="Add {model} to the harness command, or drop --model.",
        )
    logger.info("model override", extra={"track": track.name, "model": model, "own": track.model})
    return track.model_copy(update={"model": model})


def _run_sweep(
    config: Config,
    track: TrackConfig,
    executor: Executor | None,
    dry_run: bool,
    started: float,
) -> RunRecord | None:
    """Run the agent once with no work item. No source, no claim, no re-check.

    The prompt keeps the skill and the outcome instructions and omits the
    work-item block; verification and the record are the same as any run, with
    the stable sweep marker where the item id would be.
    """
    if dry_run:
        fields: dict[str, Any] = {"dry_run": True, "track": track.name, "item": SWEEP_ITEM}
        output.dry_run_header("no agent will run")
        fields |= _preview_prompt(config, track, None)
        output.dry_run_footer(action="run the sweep agent")
        fields["duration_seconds"] = round(time.monotonic() - started, 3)
        logger.info("would run", extra=fields)
        return None

    executor = executor or executors.build(config)
    run_url = executor.run_url()

    harness_config = config.harness_config()
    with tempfile.TemporaryDirectory(prefix="tina-") as tmp:
        workdir = Path(tmp)
        text = prompt.build(config.track_dir(track), None, harness.outcome_path(workdir))
        result = harness.run(harness_config, text, workdir, model=track.model, env=track.env)
        harness.capture(result.session_dir, config.artifacts_path(), SWEEP_ITEM)

    report = verify.verify(result.report)
    return _record(track.name, SWEEP_ITEM, report, result.exit_code, started, run_url)


def _preview_run(
    config: Config,
    track: TrackConfig,
    source: Source,
    item: WorkItem,
    started: float,
) -> None:
    """The dry-run half of `run_item`: the real read-only prefix, then a plan.

    It stops exactly where the real run stops. When the claim would not
    proceed there is nothing after it to describe, so the preview ends at the
    claim rather than narrating steps that would never happen.

    The message is `would run`, never `run complete`, so a collector filtering
    on `message` can never count a preview as a run. The `dry_run` marker is
    added only here, so a normal run carries no such key at all.
    """
    fields: dict[str, Any] = {"dry_run": True, "track": track.name, "item": item.id}
    output.dry_run_header("nothing will be claimed and no agent will run")

    fields["matches"] = source.matches(item.id, track.query)
    if not fields["matches"]:
        output.would(
            f"Would not run {item.id} — it no longer matches the track query;"
            f" the run would exit {OutcomeStatus.NO_ACTION_NEEDED}"
        )
        fields["effective_status"] = OutcomeStatus.NO_ACTION_NEEDED
        output.dry_run_footer(action=f"exit {OutcomeStatus.NO_ACTION_NEEDED} without claiming")
        fields["duration_seconds"] = round(time.monotonic() - started, 3)
        logger.info("would run", extra=fields)
        return

    if track.claim == "none":
        # No claim and no prognosis to preview — the query is the dedupe.
        output.would('Would skip the claim — claim = "none"; dedupe is the query\'s job')
        fields |= _preview_prompt(config, track, item)
        output.dry_run_footer(action=f"run the agent on {item.id}")
        fields["duration_seconds"] = round(time.monotonic() - started, 3)
        logger.info("would run", extra=fields)
        return

    prognosis = source.claim_prognosis(item)
    fields |= {"would_claim": prognosis.would_claim, "holder": prognosis.holder}
    if prognosis.would_claim:
        held = f"held by {prognosis.holder}" if prognosis.holder else "unassigned"
        output.would(f"Would claim {item.id} — {held}")
        fields |= _preview_prompt(config, track, item)
    else:
        output.would(
            f"Would not claim {item.id} — held by {prognosis.holder};"
            f" the run would exit {OutcomeStatus.NO_ACTION_NEEDED}"
        )
        fields["effective_status"] = OutcomeStatus.NO_ACTION_NEEDED
    output.dry_run_footer(action=f"claim {item.id} and run the agent")

    fields["duration_seconds"] = round(time.monotonic() - started, 3)
    logger.info("would run", extra=fields)


def _preview_prompt(config: Config, track: TrackConfig, item: WorkItem | None) -> dict[str, Any]:
    """Assemble the genuine prompt and render the genuine command.

    The workdir is a real one that outlives the process — a printed command
    naming a prompt file that was deleted on the way out would not be runnable,
    and being runnable is the point. It is the only thing a dry run writes, and
    it lands in the OS temp dir. Real runs keep their auto-cleaned
    `TemporaryDirectory`.
    """
    harness_config = config.harness_config()
    workdir = Path(tempfile.mkdtemp(prefix="tina-"))
    text = prompt.build(config.track_dir(track), item, harness.outcome_path(workdir))
    prompt_file = harness.write_prompt(text, workdir)
    command = harness_config.command.render(
        prompt_file, workdir, model=track.model, session_dir=harness.session_path(workdir)
    )

    output.would(f"Prompt assembled: {prompt_file} ({len(text)} chars)")
    output.would(f"Would run: {shlex.join(command)}")
    output.would("Would verify artifacts and record the outcome from the agent's outcome.json")

    return {
        "harness": harness_config.name,
        "command": command,
        "prompt_file": str(prompt_file),
        "prompt_chars": len(text),
    }


#: Effective statuses that trigger write-back: the runs a human must be told
#: about. A lying `resolved` arrives here as `needs_human` via verification.
WRITE_BACK_STATUSES = frozenset({OutcomeStatus.FAILED, OutcomeStatus.NEEDS_HUMAN})

#: Details longer than this are cut from the failure comment. The comment is
#: a pointer to the run, not a transcript of it.
_DETAILS_LIMIT = 1000


def _write_back(track: TrackConfig, source: Source, item: WorkItem, record: RunRecord) -> None:
    """Leave a trace of a bad run on the item, and stop the query matching it.

    Runs after the record is logged, so a write-back problem can never change
    the recorded outcome — and the adapters' `annotate` and `block` never
    raise (ADR-013). Clean outcomes write nothing; so does `on_failure =
    "leave"`, the default, since annotating writes to the tracker and no
    existing deployment asked for that.
    """
    if track.on_failure != "annotate" or record.effective_status not in WRITE_BACK_STATUSES:
        return
    source.annotate(item, _failure_comment(record))
    source.block(item)


def _failure_comment(record: RunRecord) -> str:
    """The effective status, the agent's details, and the log link.

    When the executor cannot name its logs the link line is simply absent —
    a comment must never say "logs unavailable" where a pointer belongs.
    """
    status = f"run ended {record.effective_status}"
    if record.report.verified is False:
        status += " (the agent reported resolved, but artifact verification failed)"
    lines = [f"tina: {status}; blocking this item from re-dispatch."]
    if record.report.details:
        details = record.report.details
        if len(details) > _DETAILS_LIMIT:
            details = details[:_DETAILS_LIMIT] + "…"
        lines.append(details)
    if record.run_url:
        lines.append(f"Run logs: {record.run_url}")
    return "\n\n".join(lines)


def _record(
    track: str,
    item: str,
    report: OutcomeReport,
    exit_code: int | None,
    started: float,
    run_url: str | None = None,
) -> RunRecord:
    record = RunRecord.build(
        track=track,
        item=item,
        report=report,
        exit_code=exit_code,
        duration_seconds=time.monotonic() - started,
        run_url=run_url,
    )
    logger.info("run complete", extra=record.model_dump(mode="json"))
    return record


def main() -> None:
    """Console script and `python -m tina` entrypoint."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
