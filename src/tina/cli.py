"""`tina dispatch` and `tina run`. Two roles, one image."""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

from tina import executors, harness, log, prompt, sources, verify
from tina.config import Config, ConfigError
from tina.config import load as load_config
from tina.executors.base import Executor, ExecutorError
from tina.models import OutcomeReport, OutcomeStatus, RunRecord
from tina.prompt import PromptError
from tina.sources.base import Source, SourceError

DEFAULT_CONFIG = "tina.toml"

logger = log.get_logger("tina")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tina",
        description="An autonomous factory: claim a work item, run an agent once, record it.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    dispatch = subcommands.add_parser(
        "dispatch", help="run the source query and enqueue up to --limit workers"
    )
    dispatch.add_argument("--workflow", required=True)
    dispatch.add_argument("--limit", type=int, default=1)
    dispatch.add_argument("--config", default=DEFAULT_CONFIG)

    run = subcommands.add_parser(
        "run", help="claim one work item, run the agent, record the outcome"
    )
    run.add_argument("--workflow", required=True)
    run.add_argument("--item", required=True)
    run.add_argument("--config", default=DEFAULT_CONFIG)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log.configure()

    try:
        config = load_config(args.config)
        if args.command == "dispatch":
            return dispatch(config, args.workflow, args.limit)
        return run(config, args.workflow, args.item)
    except (ConfigError, ExecutorError, SourceError, PromptError) as exc:
        # Tina's own failure, as opposed to an agent reporting `failed`.
        logger.error(str(exc), extra={"command": args.command})
        return 1


def dispatch(
    config: Config,
    workflow_name: str,
    limit: int,
    source: Source | None = None,
    executor: Executor | None = None,
) -> int:
    """Query, take up to `limit` items, enqueue one worker each.

    The dispatcher never runs an agent and never claims — workers claim, so a
    dispatcher that dies mid-loop leaves nothing stuck.
    """
    workflow = config.workflow(workflow_name)
    source = source or sources.build(workflow)
    executor = executor or executors.build(config)

    items = source.query(workflow.query)[: max(limit, 0)]
    logger.info(
        "dispatching",
        extra={"workflow": workflow.name, "limit": limit, "matched": len(items)},
    )
    for item in items:
        executor.enqueue(workflow.name, item.id)
        logger.info(
            "enqueued",
            extra={
                "workflow": workflow.name,
                "item": item.id,
                "url": item.url,
                "executor": config.executor,
            },
        )
    return 0


def run(
    config: Config,
    workflow_name: str,
    item_id: str,
    source: Source | None = None,
) -> int:
    """Claim one item, run the agent once, verify, record.

    Exit code reports whether *Tina* worked. An agent reporting `failed` is
    still a successful run: the outcome is data, not a process failure.
    """
    started = time.monotonic()
    workflow = config.workflow(workflow_name)
    source = source or sources.build(workflow)

    item = source.get(item_id)
    if not source.claim(item):
        logger.info(
            "already claimed",
            extra={"workflow": workflow.name, "item": item.id},
        )
        return _record(
            workflow.name,
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
        text = prompt.build(config.activity_dir(workflow), item, harness.outcome_path(workdir))
        result = harness.run(harness_config, text, workdir)

    report = verify.verify(result.report)
    return _record(workflow.name, item.id, report, result.exit_code, started)


def _record(
    workflow: str,
    item: str,
    report: OutcomeReport,
    exit_code: int | None,
    started: float,
) -> int:
    record = RunRecord.build(
        workflow=workflow,
        item=item,
        report=report,
        exit_code=exit_code,
        duration_seconds=time.monotonic() - started,
    )
    logger.info("run complete", extra=record.model_dump(mode="json"))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
