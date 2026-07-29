"""One-shot prompt assembly: activity skill + work item + outcome instructions.

Tina ships no activities. They are skills installed with napoln at image build
time; Tina only reads them off disk.
"""

from __future__ import annotations

from pathlib import Path

from tina.errors import TinaError
from tina.models import WorkItem

SKILL_FILE = "SKILL.md"


class PromptError(TinaError, RuntimeError):
    """The activity skill could not be read."""


OUTCOME_INSTRUCTIONS = """\
## Reporting the outcome

You run once. There is no follow-up turn, and nothing reads your stdout.

Before you finish, write a JSON file to exactly this path:

    {outcome_path}

The file must contain a single JSON object:

    {{
      "outcome": "resolved | no_action_needed | needs_human | failed",
      "details": "free-form prose describing what you did and why",
      "artifacts": [{{"kind": "github:pr", "url": "https://..."}}]
    }}

`outcome` must be one of exactly these four values:

- `resolved` — the work item is handled and you produced the result.
- `no_action_needed` — nothing needed doing; explain why in `details`.
- `needs_human` — you correctly concluded a person must decide. Not an error.
- `failed` — you could not complete the work. Put the reason in `details`.

`details` is prose and unmodeled; use as much of it as you want. `artifacts`
lists everything you created in another system, and may be empty. Every URL you
list under `resolved` will be fetched to confirm it exists, so do not list an
artifact you did not actually create.

If this file is missing when you exit, the run is recorded as failed.
"""


def build(activity_dir: Path, item: WorkItem, outcome_path: Path) -> str:
    """Assemble the full prompt handed to the harness."""
    skill = read_skill(activity_dir)
    return "\n".join(
        [
            skill.rstrip(),
            "",
            "## Work item",
            "",
            "This is the single work item for this run, as JSON:",
            "",
            "```json",
            item.model_dump_json(indent=2),
            "```",
            "",
            OUTCOME_INSTRUCTIONS.format(outcome_path=outcome_path),
        ]
    )


def read_skill(activity_dir: Path) -> str:
    """Read `<activity_dir>/SKILL.md`."""
    path = activity_dir / SKILL_FILE
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise PromptError(
            f"activity skill not found at {path}",
            fix="Activities are installed into the activities directory with"
            " napoln; tina ships none.",
        ) from None
    except OSError as exc:
        raise PromptError(f"could not read activity skill at {path}: {exc}") from exc
