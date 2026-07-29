"""`tina dispatch` and `tina run`. Two roles, one image.

The typer commands are a thin shell: they parse argv, load config, and turn a
`TinaError` into exit 1. The orchestration lives in `dispatch_track` and
`run_item`, which take already-built objects so callers (and tests) can inject
a source or executor.
"""

from __future__ import annotations

import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

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
) -> None:
    """Claim one work item, run the agent, record the outcome."""
    with _exit_on_tina_error("run"):
        run_item(load_config(config), track, item)


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


def run_item(
    config: Config,
    track_name: str,
    item_id: str,
    source: Source | None = None,
) -> RunRecord:
    """Claim one item, run the agent once, verify, record.

    Returns the record it logged. Every agent outcome is a successful run — the
    outcome is data, not a process failure — so this never signals via an
    exception unless Tina itself broke.
    """
    started = time.monotonic()
    track = config.track(track_name)
    source = source or sources.build(track)

    item = source.get(item_id)
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
