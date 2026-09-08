from __future__ import annotations

import json
from pathlib import Path

from tina import config, introspect
from tina.config import TrackConfig

CONFIG = """
harness = "pi"

[harnesses.pi]
command = ["pi", "--prompt-file", "{prompt_file}", "--model", "{model}"]

[vul]
source = "jira"
query = "project = VUL"
model = "sonnet"
max_concurrency = 3

[audit]
mode = "sweep"
enabled = false
model = "flash"
"""


def loaded(tmp_path: Path) -> config.Config:
    path = tmp_path / "tina.toml"
    path.write_text(CONFIG)
    return config.load(path)


def test_every_track_key_has_a_description() -> None:
    """A key added to TrackConfig without a line in DESCRIPTIONS ships undocumented."""
    assert set(TrackConfig.model_fields) == set(introspect.DESCRIPTIONS)
    assert all(introspect.DESCRIPTIONS.values())


def test_options_read_type_default_and_requiredness_from_the_model() -> None:
    by_key = {row.key: row for row in introspect.options()}

    assert by_key["mode"].type == '"queue" | "sweep"'
    assert by_key["mode"].default == '"queue"'
    assert by_key["enabled"].default == "true"
    assert by_key["max_concurrency"].type == "integer"
    assert by_key["max_concurrency"].default is None
    assert by_key["filters"].type == "table of array of strings"
    assert by_key["track"].required is True
    assert by_key["labels"].required is False


def test_markdown_renders_one_row_per_key() -> None:
    md = introspect.options_markdown()

    assert md.startswith("| Key | Type | Required | Default | Description |")
    assert md.count("\n") == len(TrackConfig.model_fields) + 2
    assert "| `on_failure` |" in md


def test_text_renders_every_key() -> None:
    text = introspect.options_text()

    for key in TrackConfig.model_fields:
        assert f"{key} (" in text


def test_json_is_the_schema_with_descriptions_folded_in() -> None:
    schema = json.loads(introspect.options_json())

    assert (
        schema["properties"]["on_failure"]["description"] == introspect.DESCRIPTIONS["on_failure"]
    )
    assert "track" in schema["required"]


def test_tracks_text_marks_disabled_tracks(tmp_path: Path) -> None:
    assert introspect.tracks_text(loaded(tmp_path)) == "audit (disabled)\nvul\n"


def test_tracks_json_carries_what_infrastructure_derives_from(tmp_path: Path) -> None:
    payload = json.loads(introspect.tracks_json(loaded(tmp_path)))

    assert list(payload["tracks"]) == ["audit", "vul"], "sorted for idempotent regeneration"
    assert payload["tracks"]["vul"] == {
        "enabled": True,
        "mode": "queue",
        "source": "jira",
        "track": "vul",
        "model": "sonnet",
        "max_concurrency": 3,
    }
    assert payload["tracks"]["audit"]["enabled"] is False
    assert payload["tracks"]["audit"]["source"] is None
