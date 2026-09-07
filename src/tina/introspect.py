"""Introspection: what is configured, rendered from the schema that parses it.

`tina tracks` lists the track tables so infrastructure can be derived from the
same file the runtime reads — one job and one schedule per track, with a CI
check that the derived file matches. `tina config-options` documents every
track key with its type, default, and meaning, read from the pydantic model
so it can never disagree with the parser.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin

from pydantic.fields import FieldInfo

from tina.config import Config, TrackConfig

#: Keys `tina config-options` documents, with the meaning of each. The field
#: definitions carry the type and default; the prose lives here so the model
#: stays readable. A key added to `TrackConfig` without a line here fails
#: `tests/unit/test_introspect.py`.
DESCRIPTIONS: dict[str, str] = {
    "name": "The table name. Identifies the track; `tina run --track <name>`, case-insensitive.",
    "mode": (
        '`"queue"` runs the source query and hands each worker one item; `"sweep"` launches '
        "one worker with no item, and the skill discovers, dedupes, and delivers the work."
    ),
    "source": 'Which tracker the queue reads: `"jira"` or `"github"`. Required for queue tracks.',
    "query": (
        "The full tracker query (JQL, or GitHub issue-search syntax). Given outright, or built "
        "from the structured inputs (`project`/`status`/`filters`/`extra`, or `repo`/`labels`) "
        "when absent — never both."
    ),
    "track": "The skill directory under `tracks_dir`. Defaults to the table name.",
    "project": "Jira structured input: the project key searched.",
    "status": (
        'Jira structured input: the status an item must be in to be eligible. `"Open"` unless '
        "the workflow names its queued status otherwise."
    ),
    "filters": (
        "Jira structured input: a table of field name to allowed values, each becoming a "
        '`"Field" in (...)` clause. Teams opt in by adding one value.'
    ),
    "extra": "Jira structured input: a predicate appended as `AND (...)`.",
    "labels": "GitHub structured input: labels an issue must carry (all of them).",
    "enabled": (
        "`false` ships the track without running it: dispatch and run refuse, `tina tracks` "
        "reports it disabled, and it is still validated."
    ),
    "model": (
        "Substituted for `{model}` in the harness command. Required exactly when the selected "
        "harness references `{model}`."
    ),
    "result": "A declaration of what the agent produces (`github:pr`). Documentation only.",
    "repo": 'GitHub: the `"owner/name"` the query and claims apply to. Required for the source.',
    "blocked_label": (
        "The label `block()` applies after a bad run. The query must exclude it, or blocked "
        "items match again. Built queries exclude it automatically."
    ),
    "blocked_transition": (
        "Jira only: `block()` transitions to this status instead of labeling. The query's own "
        "`status =` clause then excludes it."
    ),
    "max_concurrency": (
        "This track's cap on live workers. Overrides the control file's `max_concurrency`; "
        "`paused` still applies."
    ),
    "on_failure": (
        '`"leave"` retries a bad item next cycle; `"annotate"` comments the effective status '
        "and blocks it. Fires on `failed`, `needs_human`, and a `resolved` whose artifacts "
        "did not verify."
    ),
    "claim": (
        '`"assign"` the bot, `"label"` apply `claim_label`, or `"none"` — no claim, dedupe is '
        "the query's job."
    ),
    "claim_label": 'Required with `claim = "label"`. The query must exclude it.',
    "claim_transition": (
        "Jira only: a transition applied after a successful claim, so the queued status stays "
        "truthful. Built queries then offer back bot-held items still in the queued status."
    ),
    "env": (
        "Literal strings merged over the inherited environment for the harness subprocess "
        "only. Uppercase names; `TINA_*` is reserved."
    ),
}


@dataclass(frozen=True)
class Option:
    key: str
    type: str
    required: bool
    default: str | None
    description: str


def options() -> list[Option]:
    """One row per track key: name, type, requiredness, default, description."""
    rows = []
    for key, info in TrackConfig.model_fields.items():
        rows.append(
            Option(
                key=key,
                type=_type_label(info.annotation),
                required=info.is_required(),
                default=_default_label(info),
                description=DESCRIPTIONS.get(key, ""),
            )
        )
    return rows


def options_markdown() -> str:
    lines = ["| Key | Type | Required | Default | Description |", "|---|---|---|---|---|"]
    for row in options():
        default = f"`{row.default}`" if row.default is not None else "—"
        cells = [
            f"`{row.key}`",
            row.type,
            "yes" if row.required else "no",
            default,
            row.description,
        ]
        lines.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
    return "\n".join(lines) + "\n"


def options_text() -> str:
    chunks = []
    for row in options():
        qualifier = "required" if row.required else "optional"
        if row.default is not None:
            qualifier += f", default {row.default}"
        chunks.append(f"{row.key} ({row.type}; {qualifier})\n    {row.description}")
    return "\n\n".join(chunks) + "\n"


def options_json() -> str:
    schema = TrackConfig.model_json_schema()
    for key, description in DESCRIPTIONS.items():
        if key in schema.get("properties", {}):
            schema["properties"][key]["description"] = description
    return json.dumps(schema, indent=2) + "\n"


def tracks_text(config: Config) -> str:
    return "".join(
        f"{name}\n" if track.enabled else f"{name} (disabled)\n"
        for name, track in sorted(config.tracks.items())
    )


def tracks_json(config: Config) -> str:
    """One object per track, keyed by name, with the fields infrastructure
    derives from: `enabled`, `mode`, `source`, `track`, `max_concurrency`.
    Sorted, so regeneration is idempotent."""
    payload = {
        name: {
            "enabled": track.enabled,
            "mode": track.mode,
            "source": track.source,
            "track": track.track,
            "model": track.model,
            "max_concurrency": track.max_concurrency,
        }
        for name, track in sorted(config.tracks.items())
    }
    return json.dumps({"tracks": payload}, indent=2) + "\n"


def _type_label(annotation: Any) -> str:
    origin = get_origin(annotation)
    if origin is Literal:
        return " | ".join(f'"{a}"' for a in get_args(annotation))
    if origin in (Union, UnionType):
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        return " | ".join(_type_label(a) for a in non_none)
    if origin is list:
        return f"array of {_plural(_type_label(get_args(annotation)[0]))}"
    if origin is dict:
        return f"table of {_plural(_type_label(get_args(annotation)[1]))}"
    return {str: "string", bool: "boolean", int: "integer"}.get(annotation, str(annotation))


def _plural(label: str) -> str:
    return label if label.endswith("s") else f"{label}s"


def _default_label(info: FieldInfo) -> str | None:
    if info.is_required() or info.default is None:
        return None
    if info.default_factory is not None:
        return None
    value = info.default
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return f'"{value}"'
