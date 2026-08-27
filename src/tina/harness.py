"""Harness invocation.

Tina does not parse harness stdout. Each harness reports differently, and
parsing per-harness output is where swappability rots. The agent writes
`outcome.json` to a path Tina provides; the exit code is only the fallback for
"the agent died before writing" (architecture §12).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from tina.config import HarnessConfig
from tina.log import get_logger
from tina.models import OutcomeReport, OutcomeStatus

log = get_logger(__name__)

OUTCOME_FILE = "outcome.json"
PROMPT_FILE = "prompt.md"
DEFAULT_TIMEOUT = 3600.0


@dataclass(frozen=True)
class HarnessResult:
    """What the harness produced, and how its process ended."""

    report: OutcomeReport
    exit_code: int | None


def outcome_path(workdir: Path) -> Path:
    """Where the agent is told to write its report."""
    return workdir / OUTCOME_FILE


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
) -> HarnessResult:
    """Write the prompt, run the harness once, read whatever it left behind."""
    prompt_file = write_prompt(prompt, workdir)

    command = config.command.render(prompt_file, workdir, model=model)
    log.info("harness starting", extra={"harness": config.name, "command": command})

    try:
        completed = subprocess.run(
            command,
            check=False,
            cwd=workdir,
            timeout=timeout if timeout is not None else default_timeout(),
        )
    except subprocess.TimeoutExpired as exc:
        return HarnessResult(
            report=OutcomeReport(
                outcome=OutcomeStatus.FAILED,
                details=f"agent timed out after {exc.timeout:g}s",
            ),
            exit_code=None,
        )
    except OSError as exc:
        return HarnessResult(
            report=OutcomeReport(
                outcome=OutcomeStatus.FAILED,
                details=f"could not start harness {config.name!r}: {exc}",
            ),
            exit_code=None,
        )

    report = read_outcome(outcome_path(workdir), completed.returncode)
    return HarnessResult(report=report, exit_code=completed.returncode)


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
