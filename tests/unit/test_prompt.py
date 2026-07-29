from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tina import prompt
from tina.models import WorkItem


def track(tmp_path: Path, text: str = "# Remediate\n\nFix the vulnerability.\n") -> Path:
    directory = tmp_path / "tracks" / "remediate"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(text)
    return directory


def test_prompt_carries_skill_item_and_outcome_path(tmp_path: Path, work_item: WorkItem) -> None:
    outcome_path = tmp_path / "run" / "outcome.json"
    text = prompt.build(track(tmp_path), work_item, outcome_path)

    assert "Fix the vulnerability." in text
    assert str(outcome_path) in text
    assert text.index("Fix the vulnerability.") < text.index("## Work item")


def test_work_item_is_embedded_as_valid_json(tmp_path: Path, work_item: WorkItem) -> None:
    text = prompt.build(track(tmp_path), work_item, tmp_path / "outcome.json")

    block = re.search(r"```json\n(.*?)\n```", text, re.DOTALL)
    assert block is not None
    assert json.loads(block.group(1))["id"] == "VUL-1"


def test_all_four_outcomes_are_described(tmp_path: Path, work_item: WorkItem) -> None:
    text = prompt.build(track(tmp_path), work_item, tmp_path / "outcome.json")

    for status in ("resolved", "no_action_needed", "needs_human", "failed"):
        assert status in text


def test_missing_skill_points_at_napoln(tmp_path: Path, work_item: WorkItem) -> None:
    with pytest.raises(prompt.PromptError) as excinfo:
        prompt.build(tmp_path / "tracks" / "nope", work_item, tmp_path / "outcome.json")

    assert "track skill not found" in str(excinfo.value)
    assert "napoln" in excinfo.value.fix
