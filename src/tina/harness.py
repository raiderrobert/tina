"""Harness invocation.

Tina does not parse harness stdout. Each harness reports differently, and
parsing per-harness output is where swappability rots. The agent writes
`outcome.json` to a path Tina provides; the exit code is only the fallback for
"the agent died before writing" (architecture §12).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from tina.config import HarnessConfig, HarnessRetry
from tina.log import get_logger
from tina.models import OutcomeReport, OutcomeStatus
from tina.scrub import scrub

log = get_logger(__name__)

OUTCOME_FILE = "outcome.json"
PROMPT_FILE = "prompt.md"
SESSION_DIR = "session"
DEFAULT_TIMEOUT = 3600.0


@dataclass(frozen=True)
class HarnessResult:
    """What the harness produced, and how its process ended."""

    report: OutcomeReport
    exit_code: int | None
    # Where the harness left its session — transcript, tool calls, costs.
    # None when the command never references {session_dir}.
    session_dir: Path | None = None


def outcome_path(workdir: Path) -> Path:
    """Where the agent is told to write its report."""
    return workdir / OUTCOME_FILE


def session_path(workdir: Path) -> Path:
    """Where {session_dir} points for this run."""
    return workdir / SESSION_DIR


def default_timeout() -> float:
    raw = os.environ.get("TINA_HARNESS_TIMEOUT")
    if not raw:
        return DEFAULT_TIMEOUT
    try:
        return float(raw)
    except ValueError:
        log.warning("ignoring invalid TINA_HARNESS_TIMEOUT", extra={"value": raw})
        return DEFAULT_TIMEOUT


def write_prompt(prompt: str, workdir: Path) -> Path:
    """Put the prompt where the rendered command expects it, and say where.

    `run` and `tina run --dry-run` both go through here, so the file the
    preview names is the file a real run would hand the agent.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    prompt_file = workdir / PROMPT_FILE
    prompt_file.write_text(prompt, encoding="utf-8")
    return prompt_file


def run(
    config: HarnessConfig,
    prompt: str,
    workdir: Path,
    timeout: float | None = None,
    model: str | None = None,
    env: dict[str, str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> HarnessResult:
    """Write the prompt, run the harness once, read whatever it left behind.

    `env` is the track's table, merged over the inherited environment for the
    subprocess only — tina's own environment is never touched. Empty or None
    inherits unchanged.

    A command referencing {session_dir} gets a fresh directory per run; one
    that never references it gets no directory created at all.

    "Once" is per the harness's retry rules: a nonzero exit whose output
    carried a configured marker is re-run after the rule's wait, up to the
    length of its ladder. A harness with no rules is run exactly once, with
    its output inherited rather than relayed.
    """
    prompt_file = write_prompt(prompt, workdir)

    session_dir = session_path(workdir) if config.command.uses("session_dir") else None
    if session_dir is not None:
        session_dir.mkdir(parents=True, exist_ok=True)

    command = config.command.render(prompt_file, workdir, model=model, session_dir=session_dir)
    log.info("harness starting", extra={"harness": config.name, "command": command})

    try:
        returncode = _run_with_retries(
            command,
            workdir,
            os.environ | env if env else None,
            timeout if timeout is not None else default_timeout(),
            config.retry,
            sleep,
        )
    except subprocess.TimeoutExpired as exc:
        return HarnessResult(
            report=OutcomeReport(
                outcome=OutcomeStatus.FAILED,
                details=f"agent timed out after {exc.timeout:g}s",
            ),
            exit_code=None,
            session_dir=session_dir,
        )
    except OSError as exc:
        return HarnessResult(
            report=OutcomeReport(
                outcome=OutcomeStatus.FAILED,
                details=f"could not start harness {config.name!r}: {exc}",
            ),
            exit_code=None,
            session_dir=session_dir,
        )

    report = read_outcome(outcome_path(workdir), returncode)
    return HarnessResult(report=report, exit_code=returncode, session_dir=session_dir)


def _run_with_retries(
    command: list[str],
    workdir: Path,
    env: dict[str, str] | None,
    timeout: float,
    rules: list[HarnessRetry],
    sleep: Callable[[float], None],
) -> int:
    """Run the command, re-running while its output names a retryable failure.

    The first rule in configured order that matches the output decides the
    wait — so a line carrying both a specific marker and a general one is
    retried on the specific rule's schedule, and once that ladder is spent the
    failure stands rather than falling through to the vaguer rule. A clean
    exit, or one no rule matches, ends the loop.
    """
    if not rules:
        return subprocess.run(
            command, check=False, cwd=workdir, env=env, timeout=timeout
        ).returncode

    spent = [0] * len(rules)
    while True:
        returncode, output = _run_relaying(command, workdir, env, timeout)
        if returncode == 0:
            return returncode
        # The first rule that matches decides — a spent ladder on that rule ends
        # the loop rather than falling through to a vaguer rule further down.
        index = next((i for i, rule in enumerate(rules) if rule.matches(output)), None)
        if index is None or spent[index] >= len(rules[index].waits):
            return returncode
        wait = rules[index].waits[spent[index]]
        spent[index] += 1
        log.warning(
            "harness failed on a retryable condition; retrying",
            extra={"reason": rules[index].reason, "wait_seconds": wait, "exit_code": returncode},
        )
        sleep(wait)


def _run_relaying(
    command: list[str],
    workdir: Path,
    env: dict[str, str] | None,
    timeout: float,
    echo: bool = True,
) -> tuple[int, str]:
    """Run the command with its output piped, line by line.

    Piped rather than inherited so the retry markers can be seen, and — when
    `echo` is on — relayed as it arrives so a log collector still gets the run
    as it happens instead of one block at the end. Stderr, because stdout is
    Tina's JSON records. A probe turns `echo` off: its output is a verdict to
    summarize, not a run to follow.
    """
    lines: list[str] = []
    with subprocess.Popen(
        command,
        cwd=workdir,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    ) as proc:
        try:
            for line in proc.stdout or ():
                if echo:
                    sys.stderr.write(line)
                    sys.stderr.flush()
                lines.append(line)
            returncode = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise
    return returncode, "".join(lines)


PROBE_TIMEOUT = 120.0

#: The whole task a probe asks of the agent: prove the harness reaches the
#: model and the model can follow the outcome contract. Nothing else.
PROBE_PROMPT = """\
This is a connectivity probe, not a task. Do not read or change anything.

Write a JSON file to exactly this path:

    {outcome_path}

containing exactly this object and nothing else:

    {{"outcome": "no_action_needed", "details": "probe"}}

Then stop.
"""


@dataclass(frozen=True)
class ProbeResult:
    """One harness invocation with a trivial prompt: did the model answer?"""

    ok: bool
    detail: str = ""


def probe(config: HarnessConfig, model: str | None, timeout: float = PROBE_TIMEOUT) -> ProbeResult:
    """Run the harness once, for real, with a prompt whose only job is to be
    answered — the configured command, the given model, the outcome contract.

    No retries: a probe wants the verdict now. A nonzero exit whose output
    carries one of the harness's retry markers still counts as answering —
    the request reached the model and was throttled, which proves exactly
    what a probe asks — and says so. The temp directory is gone on return.
    """
    with tempfile.TemporaryDirectory(prefix="tina-probe-") as tmp:
        workdir = Path(tmp)
        prompt_file = write_prompt(PROBE_PROMPT.format(outcome_path=outcome_path(workdir)), workdir)
        session_dir = session_path(workdir) if config.command.uses("session_dir") else None
        if session_dir is not None:
            session_dir.mkdir(parents=True, exist_ok=True)
        command = config.command.render(prompt_file, workdir, model=model, session_dir=session_dir)
        try:
            returncode, output = _run_relaying(command, workdir, None, timeout, echo=False)
        except subprocess.TimeoutExpired:
            return ProbeResult(ok=False, detail=f"timed out after {timeout:g}s")
        except OSError as exc:
            return ProbeResult(ok=False, detail=f"could not start harness {config.name!r}: {exc}")
        report = read_outcome(outcome_path(workdir), returncode)

    if returncode == 0 and report.outcome is not OutcomeStatus.FAILED:
        return ProbeResult(ok=True)
    throttled = next((rule for rule in config.retry if rule.matches(output)), None)
    if throttled is not None:
        return ProbeResult(
            ok=True, detail=f"answered, throttled: {throttled.reason or 'retryable'}"
        )
    tail = " ".join(output.split())[-300:] if output.strip() else report.details
    return ProbeResult(ok=False, detail=f"exit {returncode}: {tail}")


#: Captured files whose text is scrubbed for credential shapes before it is
#: stored. Everything else is copied byte for byte.
SCRUBBED_SUFFIXES = frozenset({".jsonl", ".json", ".md", ".txt", ".log"})


def capture(session_dir: Path | None, artifacts_dir: Path | None, item: str) -> None:
    """Copy the session directory's contents to `<artifacts_dir>/<item>/`.

    Text files are scrubbed of credential material on the way (`tina.scrub`):
    a transcript records tool output verbatim, and an agent that prints its
    environment lands every token it holds in one.

    Best-effort: a failure is logged and never raised, because losing the
    evidence must not fail an otherwise-successful run. Either path being
    None means there is nothing to do, and that is not worth a warning.
    """
    if session_dir is None or artifacts_dir is None:
        return
    try:
        shutil.copytree(
            session_dir,
            artifacts_dir / artifact_name(item),
            dirs_exist_ok=True,
            copy_function=_copy_scrubbed,
        )
    except Exception as exc:
        log.warning(
            "artifact capture failed",
            extra={
                "session_dir": str(session_dir),
                "artifacts_dir": str(artifacts_dir),
                "item": item,
                "error": str(exc),
            },
        )


def artifact_name(item: str) -> str:
    """The directory an item's artifacts land in: the id, with path separators
    flattened so `owner/name#42` is one directory rather than two."""
    return item.replace("/", "__")


def _copy_scrubbed(src: str, dst: str) -> None:
    source = Path(src)
    if source.suffix.lower() not in SCRUBBED_SUFFIXES:
        shutil.copy2(src, dst)
        return
    Path(dst).write_text(
        scrub(source.read_text(encoding="utf-8", errors="replace")), encoding="utf-8"
    )


def read_outcome(path: Path, exit_code: int) -> OutcomeReport:
    """Read the agent's report, falling back to `failed` when it is unusable."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return OutcomeReport(outcome=OutcomeStatus.FAILED, details=_missing_details(exit_code))

    # Covers both malformed JSON and a well-formed document that is not a report.
    try:
        return OutcomeReport.model_validate_json(raw)
    except ValidationError as exc:
        return OutcomeReport(
            outcome=OutcomeStatus.FAILED,
            details=f"agent wrote an invalid {OUTCOME_FILE}: {exc}",
        )


def _missing_details(exit_code: int) -> str:
    if exit_code == 0:
        return f"agent exited without writing {OUTCOME_FILE}"
    return f"agent exited {exit_code} without writing {OUTCOME_FILE}"
