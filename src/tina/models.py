"""The data Tina passes around: work items in, outcome reports out."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import AnyHttpUrl, BaseModel, Field


class WorkItem(BaseModel):
    """One unit of work, normalized out of whatever tracker produced it."""

    id: str
    source: str
    title: str = ""
    description: str = ""
    # None rather than "" — a tracker that returned no link has no link, and an
    # empty string is not a URL.
    url: AnyHttpUrl | None = None
    # The untouched tracker payload, so an activity can reach fields Tina does
    # not model. Deliberately unvalidated: this is the escape hatch.
    raw: dict[str, Any] = Field(default_factory=dict)


class OutcomeStatus(StrEnum):
    """The four terminal states of a run. Models what Tina does next."""

    RESOLVED = "resolved"
    NO_ACTION_NEEDED = "no_action_needed"
    NEEDS_HUMAN = "needs_human"
    FAILED = "failed"


class Artifact(BaseModel):
    """Something the agent claims to have produced, in some other system.

    The URL is validated when outcome.json is parsed, not when verification
    fetches it: an agent that writes `"url": "TBD"` has written a broken report,
    and that is a `failed` run rather than an artifact that merely 404s.
    """

    kind: str
    url: AnyHttpUrl


class OutcomeReport(BaseModel):
    """What the agent wrote to outcome.json, plus Tina's verification verdict."""

    outcome: OutcomeStatus
    details: str = ""
    artifacts: list[Artifact] = Field(default_factory=list)
    verified: bool | None = None

    @property
    def effective_status(self) -> OutcomeStatus:
        """The agent's report is never overwritten — failed verification is
        recorded alongside it and shifts the status a human sees."""
        if self.verified is False:
            return OutcomeStatus.NEEDS_HUMAN
        return self.outcome


class RunRecord(BaseModel):
    """The final log line of `tina run`."""

    workflow: str
    item: str
    report: OutcomeReport
    effective_status: OutcomeStatus
    exit_code: int | None = None
    duration_seconds: float = 0.0

    @classmethod
    def build(
        cls,
        workflow: str,
        item: str,
        report: OutcomeReport,
        exit_code: int | None,
        duration_seconds: float,
    ) -> RunRecord:
        return cls(
            workflow=workflow,
            item=item,
            report=report,
            effective_status=report.effective_status,
            exit_code=exit_code,
            duration_seconds=round(duration_seconds, 3),
        )
