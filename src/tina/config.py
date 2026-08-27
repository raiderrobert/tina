"""TOML configuration.

A config file declares which harness and executor to use, how to invoke each
harness, and one table per track::

    harness = "pi"
    executor = "local"
    tracks_dir = "tracks"

    [harnesses.pi]
    command = ["pi", "--prompt-file", "{prompt_file}"]

    [vul]
    source = "jira"
    query = "project = VUL AND status = Open AND assignee IS EMPTY"
    track = "remediate"
    result = "github:pr"

Every table that is not `harnesses` or `executors` is a track, keyed by its
table name.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from tina.errors import TinaError

SOURCES = ("jira", "github")
EXECUTORS = ("local", "cloudrun")

#: The only substitutions a harness command line may reference.
PLACEHOLDERS = frozenset({"prompt_file", "outcome_dir", "model"})
# Hyphens are matched so that `{prompt-file}` is reported as the typo it is,
# rather than falling through to the vaguer "no {prompt_file}" complaint. JSON
# and shell brace expansions do not match: they contain quotes, colons, commas.
_PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_-]*)\}")

# Top-level scalars, as opposed to tables that define adapters or tracks.
_SCALAR_KEYS = frozenset({"harness", "executor", "tracks_dir", "control"})
_ADAPTER_TABLES = frozenset({"harnesses", "executors"})


class ConfigError(TinaError, ValueError):
    """Raised for anything wrong with a config file. Always names the file."""


class ArgvTemplate(BaseModel):
    """A harness command line with the run-specific paths still to fill in.

    Validated at config load, because both mistakes it catches are otherwise
    silent: a typo'd placeholder is passed to the agent verbatim as a literal
    argument, and a template with no `{prompt_file}` runs an agent that was
    never given the prompt.
    """

    args: list[str] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _accept_bare_list(cls, data: Any) -> Any:
        """`command = [...]` in TOML is the list itself, not a table."""
        return {"args": data} if isinstance(data, list) else data

    @model_validator(mode="after")
    def _check_placeholders(self) -> ArgvTemplate:
        used = {match.group(1) for arg in self.args for match in _PLACEHOLDER.finditer(arg)}
        unknown = sorted(used - PLACEHOLDERS)
        if unknown:
            known = ", ".join(f"{{{name}}}" for name in sorted(PLACEHOLDERS))
            named = ", ".join(f"{{{name}}}" for name in unknown)
            raise ValueError(f"unknown placeholder(s) {named}; only {known} are substituted")
        if "prompt_file" not in used:
            raise ValueError(
                "must reference {prompt_file} somewhere, or the agent never receives the prompt"
            )
        return self

    def uses(self, name: str) -> bool:
        """Whether any argument references the named placeholder."""
        return any(
            match.group(1) == name for arg in self.args for match in _PLACEHOLDER.finditer(arg)
        )

    def render(self, prompt_file: Path, outcome_dir: Path, model: str | None = None) -> list[str]:
        """Substitute the run-specific values. Everything else passes through."""
        substitutions = {"{prompt_file}": str(prompt_file), "{outcome_dir}": str(outcome_dir)}
        if model is not None:
            substitutions["{model}"] = model
        rendered = []
        for arg in self.args:
            for token, value in substitutions.items():
                arg = arg.replace(token, value)
            rendered.append(arg)
        return rendered


class HarnessConfig(BaseModel):
    """How to invoke one agent harness."""

    model_config = ConfigDict(extra="forbid")

    name: str
    command: ArgvTemplate


class CloudRunOptions(BaseModel):
    """`[executors.cloudrun]`. Which job the dispatcher creates executions of."""

    model_config = ConfigDict(extra="forbid")

    project: str = Field(min_length=1)
    region: str = Field(min_length=1)
    job: str = Field(min_length=1)

    def job_path(self) -> str:
        return f"projects/{self.project}/locations/{self.region}/jobs/{self.job}"


class ExecutorOptions(BaseModel):
    """The `[executors.*]` tables. `local` takes none, so it has no entry."""

    model_config = ConfigDict(extra="forbid")

    cloudrun: CloudRunOptions | None = None


class TrackConfig(BaseModel):
    """One `source -> skill -> result` pipeline."""

    model_config = ConfigDict(extra="forbid")

    name: str
    source: Literal["jira", "github"]
    query: str
    track: str
    # A track is on by virtue of being present; false ships it without running
    # it. Disabled tracks are still fully validated so they cannot rot.
    enabled: bool = True
    # The model the harness runs for this track, substituted for {model} in the
    # command. Required exactly when the selected harness references {model} —
    # both mismatch directions fail at load, in _validate_model. Whether the
    # model exists in the provider stays the deployment's problem.
    model: str | None = None
    # A declaration only: the agent produces the result with its own tools.
    result: str | None = None
    # GitHub Issues needs to know which repo the query and claims apply to.
    repo: str | None = None
    # The exclusion marker `block()` applies — a label on both trackers. The
    # track query has to exclude it, or blocked items match again (ADR-013).
    blocked_label: str = Field(default="tina-blocked", min_length=1)
    # What a bad run leaves on the item: "leave" retries it next cycle;
    # "annotate" comments the effective status and applies `blocked_label`.
    on_failure: Literal["leave", "annotate"] = "leave"
    # How the worker claims (ADR-014): "assign" the bot, apply `claim_label`,
    # or "none" — no claim, dedupe is the query's job.
    claim: Literal["assign", "none", "label"] = "assign"
    claim_label: str | None = None
    # A Jira transition applied after a successful claim, so the queued status
    # stays truthful and humans can requeue by transition.
    claim_transition: str | None = None

    @model_validator(mode="after")
    def _check_claim_policy(self) -> TrackConfig:
        if self.claim == "label" and not self.claim_label:
            raise ValueError('claim = "label" requires claim_label')
        if self.claim != "label" and self.claim_label is not None:
            raise ValueError('claim_label only applies with claim = "label"')
        if self.claim_transition is not None:
            if self.source != "jira":
                raise ValueError("claim_transition only applies to jira tracks")
            if self.claim == "none":
                raise ValueError(
                    'claim_transition cannot apply under claim = "none" — nothing is ever claimed'
                )
        return self

    @field_validator("model")
    @classmethod
    def _check_model_shape(cls, value: str | None) -> str | None:
        if value is not None and (not value or any(c.isspace() for c in value)):
            raise ValueError("model must be non-empty and contain no whitespace")
        return value


class Config(BaseModel):
    """A parsed config file."""

    path: Path
    harness: str
    executor: str = "local"
    tracks_dir: Path = Path("tracks")
    # Where the control file lives, when the deployment does not use
    # TINA_CONTROL. None means no control plane configured here.
    control: Path | None = None
    harnesses: dict[str, HarnessConfig] = Field(default_factory=dict)
    executors: ExecutorOptions = Field(default_factory=ExecutorOptions)
    tracks: dict[str, TrackConfig] = Field(default_factory=dict)

    def harness_config(self) -> HarnessConfig:
        return self.harnesses[self.harness]

    def track(self, name: str) -> TrackConfig:
        try:
            return self.tracks[name]
        except KeyError:
            known = ", ".join(sorted(self.tracks)) or "none"
            raise ConfigError(f"{self.path}: no track named {name!r} (defined: {known})") from None

    def cloudrun_options(self) -> CloudRunOptions:
        if self.executors.cloudrun is None:
            raise ConfigError(
                f"{self.path}: executor 'cloudrun' requires an [executors.cloudrun] table",
                fix="Add an [executors.cloudrun] table with project, region, and job keys.",
            )
        return self.executors.cloudrun

    def track_dir(self, track: TrackConfig) -> Path:
        """Absolute path of the track's skill, resolved against the config file."""
        base = self.tracks_dir
        if not base.is_absolute():
            base = self.path.parent / base
        return base / track.track

    def control_path(self) -> Path | None:
        """Absolute path of the control file, resolved against the config file."""
        if self.control is None or self.control.is_absolute():
            return self.control
        return self.path.parent / self.control


def load(path: Path | str) -> Config:
    """Read and validate a config file. Fails fast with the file name in the message."""
    path = Path(path)
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"{path}: config file not found") from None
    except tomllib.TOMLDecodeError as exc:
        fix = ""
        if "overwrite" in str(exc):
            # The common one: `harness = "pi"` followed by `[harness.pi]`. TOML
            # forbids reusing a scalar key as a table, so definitions live under
            # the plural `[harnesses.*]` / `[executors.*]`.
            fix = (
                "harness/executor select an adapter by name; define them under"
                " [harnesses.<name>] and [executors.<name>]"
            )
        raise ConfigError(f"{path}: invalid TOML: {exc}", fix=fix) from None
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

    tracks = {}
    for name, table in raw.items():
        if name in _SCALAR_KEYS or name in _ADAPTER_TABLES:
            continue
        if not isinstance(table, dict):
            raise ConfigError(
                f"{path}: unexpected top-level key {name!r}; expected one of "
                f"{', '.join(sorted(_SCALAR_KEYS))} or a track table"
            )
        tracks[name] = _build(
            TrackConfig,
            {"name": name, "track": name, **table},
            path,
            f"[{name}]",
        )

    config = _build(
        Config,
        {
            "path": path,
            "harness": raw["harness"],
            "executor": raw.get("executor", "local"),
            "tracks_dir": raw.get("tracks_dir", "tracks"),
            "control": raw.get("control"),
            "harnesses": harnesses,
            "executors": dict(_tables(raw.get("executors", {}), path, "executors")),
            "tracks": tracks,
        },
        path,
        "config",
    )
    _validate_names(config)
    _validate_model(config)
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


def _build[M: BaseModel](model: type[M], data: dict[str, Any], path: Path, where: str) -> M:
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
    for track in config.tracks.values():
        if track.source == "github" and not track.repo:
            raise ConfigError(
                f"{config.path}: [{track.name}]: source 'github' requires repo = \"owner/name\""
            )


def _validate_model(config: Config) -> None:
    """Both mismatch directions between {model} and the track key fail at load.

    A command referencing {model} with no track value would run the agent with
    the literal `{model}` as an argument; a track value under a command that
    never references it would silently not reach the harness.
    """
    uses_model = config.harness_config().command.uses("model")
    for track in config.tracks.values():
        if uses_model and track.model is None:
            raise ConfigError(
                f"{config.path}: [{track.name}]: harness {config.harness!r} references"
                " {model} but the track sets no model"
            )
        if not uses_model and track.model is not None:
            raise ConfigError(
                f"{config.path}: [{track.name}]: model is set but harness"
                f" {config.harness!r} never references {{model}}"
            )
