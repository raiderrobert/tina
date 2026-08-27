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


def test_prompt_opens_by_naming_the_skill_root(tmp_path: Path, work_item: WorkItem) -> None:
    directory = track(tmp_path)
    text = prompt.build(directory, work_item, tmp_path / "outcome.json")

    head = "\n".join(text.splitlines()[:4])
    assert str(directory.resolve()) in head
    assert text.index(str(directory.resolve())) < text.index("Fix the vulnerability.")


def test_frontmatter_is_stripped_before_inlining(tmp_path: Path, work_item: WorkItem) -> None:
    skill = "---\nname: remediate\ndescription: Fixes CVEs.\n---\n\nFix the vulnerability.\n"
    text = prompt.build(track(tmp_path, skill), work_item, tmp_path / "outcome.json")

    assert "name: remediate" not in text
    assert "Fix the vulnerability." in text


def test_a_horizontal_rule_mid_skill_is_not_frontmatter(
    tmp_path: Path, work_item: WorkItem
) -> None:
    skill = "# Remediate\n\nFix the vulnerability.\n\n---\n\nText after the rule.\n"
    text = prompt.build(track(tmp_path, skill), work_item, tmp_path / "outcome.json")

    assert "Text after the rule." in text
    assert "\n---\n" in text


def test_unclosed_frontmatter_is_left_as_is(tmp_path: Path, work_item: WorkItem) -> None:
    skill = "---\nname: remediate\nno closing delimiter\n"
    text = prompt.build(track(tmp_path, skill), work_item, tmp_path / "outcome.json")

    assert "no closing delimiter" in text


def test_a_sweep_prompt_omits_the_work_item_block(tmp_path: Path) -> None:
    """No item means no block at all, not an empty one."""
    outcome_path = tmp_path / "run" / "outcome.json"
    text = prompt.build(track(tmp_path), None, outcome_path)

    assert "## Work item" not in text
    assert "```json" not in text
    assert "Fix the vulnerability." in text
    assert str(outcome_path) in text, "the outcome contract is unchanged"


def test_a_sweep_prompt_still_opens_with_the_skill_root(tmp_path: Path) -> None:
    directory = track(tmp_path)
    text = prompt.build(directory, None, tmp_path / "outcome.json")

    assert str(directory.resolve()) in "\n".join(text.splitlines()[:4])


def test_missing_skill_points_at_napoln(tmp_path: Path, work_item: WorkItem) -> None:
    with pytest.raises(prompt.PromptError) as excinfo:
        prompt.build(tmp_path / "tracks" / "nope", work_item, tmp_path / "outcome.json")

    assert "track skill not found" in str(excinfo.value)
    assert "napoln" in excinfo.value.fix
