"""`tina dispatch`, `tina run`, and `tina status`. Two roles, one image.

The typer commands are a thin shell: they parse argv, load config, and turn a
`TinaError` into exit 1. The orchestration lives in `dispatch_track`,
`run_item`, and `status_track`, which take already-built objects so callers
(and tests) can inject a source or executor.
"""

from __future__ import annotations

import shlex
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any

import typer

from tina import executors, harness, log, output, prompt, sources, verify
from tina.config import Config, TrackConfig
from tina.config import load as load_config
from tina.errors import TinaError
from tina.executors.base import Executor
from tina.models import OutcomeReport, OutcomeStatus, RunRecord, WorkItem
from tina.sources.base import Source

DEFAULT_CONFIG = Path("tina.toml")

logger = log.get_logger("tina")

app = typer.Typer(
    name="tina",
    help="An autonomous factory: claim a work item, run an agent once, record it.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)

TrackOption = Annotated[str, typer.Option("--track", help="Track table in the config.")]
ConfigOption = Annotated[Path, typer.Option("--config", help="Path to the TOML config file.")]


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
        int, typer.Option("--limit", help="Maximum number of workers to enqueue.")
    ] = 1,
    config: ConfigOption = DEFAULT_CONFIG,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview the matched items without enqueueing anything."),
    ] = False,
) -> None:
    """Run the source query and enqueue up to --limit workers."""
    with _exit_on_tina_error("dispatch"):
        dispatch_track(load_config(config), track, limit, dry_run=dry_run)


@app.command()
def run(
    track: TrackOption,
    item: Annotated[str, typer.Option("--item", help="Tracker identifier of the work item.")],
    config: ConfigOption = DEFAULT_CONFIG,
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
        run_item(load_config(config), track, item, dry_run=dry_run)


@app.command()
def status(
    track: TrackOption,
    config: ConfigOption = DEFAULT_CONFIG,
) -> None:
    """Report how many items are waiting and how many workers hold."""
    with _exit_on_tina_error("status"):
        status_track(load_config(config), track)


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
    limit: int,
    source: Source | None = None,
    executor: Executor | None = None,
    dry_run: bool = False,
) -> None:
    """Query, take up to `limit` items, enqueue one worker each.

    The dispatcher never runs an agent and never claims — workers claim, so a
    dispatcher that dies mid-loop leaves nothing stuck.

    `dry_run` moves the boundary to the last step only: the real source is built
    and the real query runs against the live tracker, but no executor is ever
    constructed. That a preview enqueues nothing follows from the absence of an
    executor, not from a branch inside the loop that nobody took. The one thing
    it therefore cannot report is a `cloudrun` executor with no
    `[executors.cloudrun]` table — a table that is present but incomplete still
    fails at config load, whichever mode this runs in.
    """
    track = config.track(track_name)
    source = source or sources.build(track)
    if dry_run:
        _preview(config, track, source.query(track.query)[: max(limit, 0)], limit)
        return

    executor = executor or executors.build(config)
    items = source.query(track.query)[: max(limit, 0)]
    logger.info(
        "dispatching",
        extra={"track": track.name, "limit": limit, "matched": len(items)},
    )
    for item in items:
        executor.enqueue(track.name, item.id)
        logger.info("enqueued", extra=_item_fields(track.name, item, config.executor))


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


def _preview(config: Config, track: TrackConfig, items: list[WorkItem], limit: int) -> None:
    """The dry-run half: same query, same fields, no executor and no enqueue.

    The message is `would enqueue`, never `enqueued`, so a collector filtering
    on `message` can never count a preview as a real dispatch. The `dry_run`
    marker is added only here, so a normal dispatch carries no such key at all.
    """
    logger.info(
        "dispatching",
        extra={"track": track.name, "limit": limit, "matched": len(items), "dry_run": True},
    )
    output.dry_run_header()
    for item in items:
        line = f"Would enqueue {item.id} via {config.executor}"
        output.would(f"{line} — {item.title}" if item.title else line)
        logger.info(
            "would enqueue",
            extra=_item_fields(track.name, item, config.executor) | {"dry_run": True},
        )
    output.dry_run_footer(f"{len(items)} items matched (limit {limit}).")


def status_track(config: Config, track_name: str, source: Source | None = None) -> None:
    """Two counts off two tracker queries. Reads no local state and writes nothing.

    The counts are two halves of one question: the same configured query, once
    as `dispatch` runs it and once with its unclaimed clause inverted by the
    adapter. Nothing is claimed and no executor is ever constructed — which is
    the guarantee, since there is no call here that could do either.
    """
    track = config.track(track_name)
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
    item_id: str,
    source: Source | None = None,
    dry_run: bool = False,
) -> RunRecord | None:
    """Claim one item, run the agent once, verify, record.

    Returns the record it logged. Every agent outcome is a successful run — the
    outcome is data, not a process failure — so this never signals via an
    exception unless Tina itself broke.

    `dry_run` returns `None`, because a preview produced no run and there is no
    record to hand back. The prefix up to the claim is executed for real, so
    the preview's fidelity comes from doing the read-only work rather than
    from describing it.
    """
    started = time.monotonic()
    track = config.track(track_name)
    source = source or sources.build(track)

    item = source.get(item_id)
    if dry_run:
        _preview_run(config, track, source, item, started)
        return None

    if not source.claim(item):
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
        )

    harness_config = config.harness_config()
    with tempfile.TemporaryDirectory(prefix="tina-") as tmp:
        workdir = Path(tmp)
        text = prompt.build(config.track_dir(track), item, harness.outcome_path(workdir))
        result = harness.run(harness_config, text, workdir)

    report = verify.verify(result.report)
    return _record(track.name, item.id, report, result.exit_code, started)


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
    prognosis = source.claim_prognosis(item)
    fields: dict[str, Any] = {
        "dry_run": True,
        "track": track.name,
        "item": item.id,
        "would_claim": prognosis.would_claim,
        "holder": prognosis.holder,
    }

    output.dry_run_header("nothing will be claimed and no agent will run")
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


def _preview_prompt(config: Config, track: TrackConfig, item: WorkItem) -> dict[str, Any]:
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
    command = harness_config.command.render(prompt_file, workdir)

    output.would(f"Prompt assembled: {prompt_file} ({len(text)} chars)")
    output.would(f"Would run: {shlex.join(command)}")
    output.would("Would verify artifacts and record the outcome from the agent's outcome.json")

    return {
        "harness": harness_config.name,
        "command": command,
        "prompt_file": str(prompt_file),
        "prompt_chars": len(text),
    }


def _record(
    track: str,
    item: str,
    report: OutcomeReport,
    exit_code: int | None,
    started: float,
) -> RunRecord:
    record = RunRecord.build(
        track=track,
        item=item,
        report=report,
        exit_code=exit_code,
        duration_seconds=time.monotonic() - started,
    )
    logger.info("run complete", extra=record.model_dump(mode="json"))
    return record


def main() -> None:
    """Console script and `python -m tina` entrypoint."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
