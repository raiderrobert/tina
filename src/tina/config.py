"""TOML configuration.

A config file declares which harness and executor to use, how to invoke each
harness, and one table per workflow::

    harness = "pi"
    executor = "local"
    activities_dir = "activities"

    [harnesses.pi]
    command = ["pi", "--prompt-file", "{prompt_file}"]

    [vul]
    source = "jira"
    query = "project = VUL AND status = Open AND assignee IS EMPTY"
    activity = "remediate"
    result = "github:pr"

Every table that is not `harnesses` or `executors` is a workflow, keyed by its
table name.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tina.errors import TinaError

SOURCES = ("jira", "github")
EXECUTORS = ("local", "cloudrun")

# Top-level scalars, as opposed to tables that define adapters or workflows.
_SCALAR_KEYS = frozenset({"harness", "executor", "activities_dir"})
_ADAPTER_TABLES = frozenset({"harnesses", "executors"})


class ConfigError(TinaError, ValueError):
    """Raised for anything wrong with a config file. Always names the file."""


class HarnessConfig(BaseModel):
    """How to invoke one agent harness.

    `command` is an argv template. `{prompt_file}` and `{outcome_dir}` are
    substituted per run; every other element is passed through verbatim.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    command: list[str] = Field(min_length=1)


class WorkflowConfig(BaseModel):
    """One `source -> activity -> result` pipeline."""

    model_config = ConfigDict(extra="forbid")

    name: str
    source: Literal["jira", "github"]
    query: str
    activity: str
    # A declaration only: the agent produces the result with its own tools.
    result: str | None = None
    # GitHub Issues needs to know which repo the query and claims apply to.
    repo: str | None = None


class Config(BaseModel):
    """A parsed config file."""

    path: Path
    harness: str
    executor: str = "local"
    activities_dir: Path = Path("activities")
    harnesses: dict[str, HarnessConfig] = Field(default_factory=dict)
    executor_options: dict[str, dict[str, Any]] = Field(default_factory=dict)
    workflows: dict[str, WorkflowConfig] = Field(default_factory=dict)

    def harness_config(self) -> HarnessConfig:
        return self.harnesses[self.harness]

    def workflow(self, name: str) -> WorkflowConfig:
        try:
            return self.workflows[name]
        except KeyError:
            known = ", ".join(sorted(self.workflows)) or "none"
            raise ConfigError(
                f"{self.path}: no workflow named {name!r} (defined: {known})"
            ) from None

    def executor_config(self) -> dict[str, Any]:
        return self.executor_options.get(self.executor, {})

    def activity_dir(self, workflow: WorkflowConfig) -> Path:
        """Absolute path of the activity skill, resolved against the config file."""
        base = self.activities_dir
        if not base.is_absolute():
            base = self.path.parent / base
        return base / workflow.activity


def load(path: Path | str) -> Config:
    """Read and validate a config file. Fails fast with the file name in the message."""
    path = Path(path)
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"{path}: config file not found") from None
    except tomllib.TOMLDecodeError as exc:
        hint = ""
        if "overwrite" in str(exc):
            # The common one: `harness = "pi"` followed by `[harness.pi]`. TOML
            # forbids reusing a scalar key as a table, so definitions live under
            # the plural `[harnesses.*]` / `[executors.*]`.
            hint = (
                " (harness/executor select an adapter by name; define them under"
                " [harnesses.<name>] and [executors.<name>])"
            )
        raise ConfigError(f"{path}: invalid TOML: {exc}{hint}") from None
    return parse(raw, path)


def parse(raw: dict[str, Any], path: Path | str = "<config>") -> Config:
    """Build a Config from an already-decoded TOML mapping."""
    path = Path(path)

    if "harness" not in raw:
        raise ConfigError(f"{path}: missing required top-level key 'harness'")

    harnesses = {
        name: _build(HarnessConfig, {"name": name, **table}, path, f"[harnesses.{name}]")
        for name, table in _tables(raw.get("harnesses", {}), path, "harnesses")
    }

    workflows = {}
    for name, table in raw.items():
        if name in _SCALAR_KEYS or name in _ADAPTER_TABLES:
            continue
        if not isinstance(table, dict):
            raise ConfigError(
                f"{path}: unexpected top-level key {name!r}; expected one of "
                f"{', '.join(sorted(_SCALAR_KEYS))} or a workflow table"
            )
        workflows[name] = _build(
            WorkflowConfig,
            {"name": name, "activity": name, **table},
            path,
            f"[{name}]",
        )

    config = _build(
        Config,
        {
            "path": path,
            "harness": raw["harness"],
            "executor": raw.get("executor", "local"),
            "activities_dir": raw.get("activities_dir", "activities"),
            "harnesses": harnesses,
            "executor_options": dict(_tables(raw.get("executors", {}), path, "executors")),
            "workflows": workflows,
        },
        path,
        "config",
    )
    _validate_names(config)
    return config


def _tables(value: Any, path: Path, key: str) -> list[tuple[str, dict[str, Any]]]:
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: [{key}] must be a table of named tables")
    tables: list[tuple[str, dict[str, Any]]] = []
    for name, table in value.items():
        if not isinstance(table, dict):
            raise ConfigError(f"{path}: [{key}.{name}] must be a table")
        tables.append((str(name), table))
    return tables


def _build(model: type, data: dict[str, Any], path: Path, where: str) -> Any:
    try:
        return model(**data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or where}: {err['msg']}" for err in exc.errors()
        )
        raise ConfigError(f"{path}: {where}: {problems}") from None


def _validate_names(config: Config) -> None:
    """Unknown adapter names are a config bug, not a runtime surprise."""
    if config.harness not in config.harnesses:
        known = ", ".join(sorted(config.harnesses)) or "none"
        raise ConfigError(
            f"{config.path}: harness {config.harness!r} has no [harnesses.{config.harness}] "
            f"table (defined: {known})"
        )
    if config.executor not in EXECUTORS:
        raise ConfigError(
            f"{config.path}: unknown executor {config.executor!r} "
            f"(supported: {', '.join(EXECUTORS)})"
        )
    for workflow in config.workflows.values():
        if workflow.source == "github" and not workflow.repo:
            raise ConfigError(
                f"{config.path}: [{workflow.name}]: source 'github' requires repo = \"owner/name\""
            )
