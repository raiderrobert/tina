"""TOML configuration.

A config file declares which harness and executor to use, how to invoke each
harness, and one table per track::

    harness = "pi"
    executor = "local"
    tracks_dir = "tracks"

    [harnesses.pi]
    command = ["pi", "-p", "@{prompt_file}"]

    [vul]
    source = "jira"
    project = "VUL"
    track = "remediate"
    result = "github:pr"

Every table that is not `harnesses` or `executors` is a track, keyed by its
table name.

A track gives the parts of its query and Tina builds it (`tina.query`): a
Jira `project` plus `filters`, or a GitHub `repo` plus `labels`. Onboarding a
team is then one array edit. There is no raw query key: every node Tina has
to rewrite is structured, and `extra` carries whatever else a source accepts.

Three environment variables override the top-level paths, so one image runs
against configs mounted anywhere: `TINA_TRACKS_DIR`, `TINA_ARTIFACTS_DIR`, and
— read by `tina.control`, not here — `TINA_CONTROL`. A program embedding Tina
passes the same three as keyword arguments to `load` instead, under whatever
names its own environment uses.
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from tina import query as query_ir
from tina.errors import TinaError

SOURCES = ("jira", "github")
EXECUTORS = ("local", "cloudrun")

#: The only substitutions a harness command line may reference.
PLACEHOLDERS = frozenset({"prompt_file", "outcome_dir", "model", "session_dir"})
# Hyphens are matched so that `{prompt-file}` is reported as the typo it is,
# rather than falling through to the vaguer "no {prompt_file}" complaint. JSON
# and shell brace expansions do not match: they contain quotes, colons, commas.
_PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_-]*)\}")

# Top-level scalars, as opposed to tables that define adapters or tracks.
_SCALAR_KEYS = frozenset(
    {"harness", "executor", "tracks_dir", "control", "artifacts_dir", "models"}
)
_ADAPTER_TABLES = frozenset({"harnesses", "executors"})

#: Environment overrides for the top-level paths. One image, many mounts.
TRACKS_DIR_VAR = "TINA_TRACKS_DIR"
ARTIFACTS_DIR_VAR = "TINA_ARTIFACTS_DIR"

#: The placeholder a Cloud Run `job` may carry, for one job per track.
TRACK_PLACEHOLDER = "{track}"

#: The namespace of environment variables tina itself owns (TINA_CONTROL,
#: TINA_HARNESS_TIMEOUT, ...). A track shadowing one would change tina's
#: behavior out from under the deployment, so the collision fails at load.
RESERVED_ENV_PREFIX = "TINA_"
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


#: Keys that configure the queue a sweep track does not have. Setting any of
#: them on a sweep entry — even to its default value — fails at load, named.
_QUEUE_ONLY_KEYS = frozenset(
    {
        "source",
        "repo",
        "claim",
        "claim_label",
        "claim_transition",
        "on_failure",
        "blocked_label",
        "blocked_transition",
        "max_concurrency",
        "project",
        "status",
        "filters",
        "extra",
        "labels",
    }
)

#: The removed raw-query key. Named so the pointer beats "extra inputs".
_REMOVED_QUERY_KEYS = ("query", "jql")

# Values the query compilers interpolate. Validated here so the builders can
# concatenate without escaping: a team name with a quote in it is at best a
# broken query, at worst an injected one.
_JIRA_PROJECT = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_JIRA_FIELD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-\[\]\.]*$")
_JIRA_VALUE = re.compile(r"^[A-Za-z0-9 _\-'&.:/]+$")
_GITHUB_REPO = re.compile(r"^[\w.-]+/[\w.-]+$")
_GITHUB_LABEL = re.compile(r'^[^"\s][^"]*$')


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

    def render(
        self,
        prompt_file: Path,
        outcome_dir: Path,
        model: str | None = None,
        session_dir: Path | None = None,
    ) -> list[str]:
        """Substitute the run-specific values. Everything else passes through."""
        substitutions = {"{prompt_file}": str(prompt_file), "{outcome_dir}": str(outcome_dir)}
        if model is not None:
            substitutions["{model}"] = model
        if session_dir is not None:
            substitutions["{session_dir}"] = str(session_dir)
        rendered = []
        for arg in self.args:
            for token, value in substitutions.items():
                arg = arg.replace(token, value)
            rendered.append(arg)
        return rendered


class HarnessRetry(BaseModel):
    """One class of harness failure worth re-running, and the waits to spend.

    A model provider that sheds load exits the harness nonzero for a condition
    that clears on its own. The rule names the output text that identifies
    the condition and a ladder of waits — one retry per entry — so the run
    survives the blip instead of failing the item. Rules are tried in the
    order configured: put the most specific first, since a quota line often
    also carries the words a capacity line does.
    """

    model_config = ConfigDict(extra="forbid")

    markers: list[str] = Field(min_length=1)
    waits: list[float] = Field(min_length=1)
    reason: str = ""

    def matches(self, text: str) -> bool:
        return any(marker in text for marker in self.markers)


class HarnessConfig(BaseModel):
    """How to invoke one agent harness."""

    model_config = ConfigDict(extra="forbid")

    name: str
    command: ArgvTemplate
    # Output markers that mean "run it again", with the waits between tries.
    # Empty means a nonzero exit is final. When any rule is set, the harness's
    # output is relayed line by line so the markers can be seen — it still
    # reaches the log, just not through Tina's stdout (which is JSON).
    retry: list[HarnessRetry] = Field(default_factory=list)


class CloudRunOptions(BaseModel):
    """`[executors.cloudrun]`. Which job the dispatcher creates executions of."""

    model_config = ConfigDict(extra="forbid")

    project: str = Field(min_length=1)
    region: str = Field(min_length=1)
    # May carry `{track}` for one job per track — how a deployment gives each
    # track its own machine size and timeout without Tina knowing either.
    job: str = Field(min_length=1)

    def job_name(self, track: str) -> str:
        return self.job.replace(TRACK_PLACEHOLDER, track)

    def job_path(self, track: str = "") -> str:
        return f"projects/{self.project}/locations/{self.region}/jobs/{self.job_name(track)}"


class ExecutorOptions(BaseModel):
    """The `[executors.*]` tables. `local` takes none, so it has no entry."""

    model_config = ConfigDict(extra="forbid")

    cloudrun: CloudRunOptions | None = None


class TrackConfig(BaseModel):
    """One `source -> skill -> result` pipeline."""

    model_config = ConfigDict(extra="forbid")

    name: str
    # How the track gets work: "queue" runs the source query and hands each
    # worker one item; "sweep" launches one worker with no item, and the skill
    # discovers, dedupes, and delivers the work itself.
    mode: Literal["queue", "sweep"] = "queue"
    # Required for queue tracks; a sweep has neither, enforced in _check_mode.
    source: Literal["jira", "github"] | None = None
    track: str
    # The parts of the query (`tina.query.build`). Which optional parts a
    # source accepts is its declared feature set; a mismatch fails in
    # _check_mode. `project` is Jira's scope; GitHub's is `repo`, below.
    project: str | None = None
    # The status an item must be in: the tracker's queued status, "Open"
    # unless the workflow names it otherwise.
    status: str | None = Field(default=None, min_length=1)
    # One `"Field" in (...)` clause per entry.
    filters: dict[str, list[str]] = Field(default_factory=dict)
    # Native predicate text the compiler appends verbatim and never reads.
    extra: str | None = Field(default=None, min_length=1)
    # Labels an item must carry, all of them.
    labels: list[str] = Field(default_factory=list)
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
    # Jira only: `block()` transitions to this status instead of labeling. A
    # status the query's own `status =` clause already excludes, so the query
    # needs no label guard — and humans see the item where they expect it.
    blocked_transition: str | None = Field(default=None, min_length=1)
    # This track's own cap on live workers. A track that declares one is not
    # bounded by the control file's `max_concurrency` — it opted out of the
    # fleet knob — but `paused` still stops it like any other.
    max_concurrency: int | None = Field(default=None, gt=0, strict=True)
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
    # Literal strings merged over the inherited environment for the harness
    # subprocess only — how a track skill's scripts get configured.
    env: dict[str, str] = Field(default_factory=dict)

    @property
    def query(self) -> query_ir.Query:
        """The track's predicate tree, built from the parts above.

        A property rather than a key: nothing in the file spells the query,
        so `tina config-options` does not list it. `tina validate` renders
        the native string through the source's `compile`.
        """
        return query_ir.build(self)

    @model_validator(mode="before")
    @classmethod
    def _reject_raw_query(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for key in _REMOVED_QUERY_KEYS:
                if key in data:
                    raise ValueError(
                        f"{key} is not a key; give the parts instead"
                        " (project/status/filters/extra, or repo/labels)"
                    )
        return data

    @model_validator(mode="after")
    def _check_mode(self) -> TrackConfig:
        if self.mode == "sweep":
            stray = sorted(set(_QUEUE_ONLY_KEYS) & self.model_fields_set)
            if stray:
                raise ValueError(f'mode = "sweep" has no queue; remove: {", ".join(stray)}')
            return self
        if self.source is None:
            raise ValueError('mode = "queue" requires source')
        if self.source == "jira" and self.project is None:
            raise ValueError('source = "jira" requires project')
        if self.source != "jira":
            if self.project is not None:
                raise ValueError("project only applies to jira tracks; the scope is repo")
            if self.blocked_transition is not None:
                raise ValueError("blocked_transition only applies to jira tracks")
        unsupported = sorted(self.query.features() - query_ir.SOURCE_FEATURES[self.source])
        if unsupported:
            raise ValueError(f'source = "{self.source}" does not support {", ".join(unsupported)}')
        return self

    @field_validator("project")
    @classmethod
    def _check_project(cls, value: str | None) -> str | None:
        if value is not None and not _JIRA_PROJECT.fullmatch(value):
            raise ValueError(f"project {value!r} must be a Jira project key (letters, digits, _)")
        return value

    @field_validator("filters")
    @classmethod
    def _check_filters(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        for field, values in value.items():
            if not _JIRA_FIELD.fullmatch(field):
                raise ValueError(f"filter field {field!r} contains characters JQL cannot quote")
            if not values:
                raise ValueError(f"filter {field!r} must list at least one value")
            for item in values:
                if not _JIRA_VALUE.fullmatch(item):
                    raise ValueError(
                        f"filter value {item!r} for {field!r} may only contain letters, digits,"
                        " spaces, and - _ ' & . : /"
                    )
        return value

    @field_validator("repo")
    @classmethod
    def _check_repo(cls, value: str | None) -> str | None:
        if value is not None and not _GITHUB_REPO.fullmatch(value):
            raise ValueError(f'repo {value!r} must be "owner/name"')
        return value

    @field_validator("labels")
    @classmethod
    def _check_labels(cls, value: list[str]) -> list[str]:
        for label in value:
            if not _GITHUB_LABEL.fullmatch(label):
                raise ValueError(f"label {label!r} may not contain quotes or start with whitespace")
        return value

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

    @field_validator("env")
    @classmethod
    def _check_env_names(cls, value: dict[str, str]) -> dict[str, str]:
        for name in value:
            if not _ENV_NAME.fullmatch(name):
                raise ValueError(
                    f"env name {name!r} must be uppercase letters, digits, and"
                    " underscores, starting with a letter"
                )
            if name.startswith(RESERVED_ENV_PREFIX):
                raise ValueError(
                    f"env name {name!r} collides with tina's own {RESERVED_ENV_PREFIX}* variables"
                )
        return value

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
    # The models the harness may run, as the exact strings `{model}` takes.
    # Empty means unconstrained. When set, every track's `model` and every
    # `run --model` override must be one of them: a model the provider does
    # not serve this deployment fails every run of the track that names it,
    # from inside the harness where nobody is looking. The list is the one
    # place that says which models this deployment has enabled — and what a
    # live probe (`doctor`) has to prove answers.
    models: list[str] = Field(default_factory=list)
    tracks_dir: Path = Path("tracks")
    # Where the control file lives, when the deployment does not use
    # TINA_CONTROL. None means no control plane configured here.
    control: Path | None = None
    # Where each run's session directory gets copied after the harness exits.
    # None skips the copy; {session_dir} still substitutes either way.
    artifacts_dir: Path | None = None
    harnesses: dict[str, HarnessConfig] = Field(default_factory=dict)
    executors: ExecutorOptions = Field(default_factory=ExecutorOptions)
    tracks: dict[str, TrackConfig] = Field(default_factory=dict)

    def harness_config(self) -> HarnessConfig:
        return self.harnesses[self.harness]

    @field_validator("models")
    @classmethod
    def _check_models(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        for model in value:
            if not model or any(c.isspace() for c in model):
                raise ValueError(f"model {model!r} must be non-empty and contain no whitespace")
            if model in seen:
                raise ValueError(f"model {model!r} is listed twice")
            seen.add(model)
        return value

    def allows_model(self, model: str) -> bool:
        """Whether `model` may run here: listed, or nothing is listed."""
        return not self.models or model in self.models

    def track(self, name: str) -> TrackConfig:
        """The track table named, matched case-insensitively.

        Schedulers and IaC tend to upper-case names on their way through an
        environment variable; the table key is still the canonical spelling.
        """
        if name in self.tracks:
            return self.tracks[name]
        lowered = name.lower()
        for key, track in self.tracks.items():
            if key.lower() == lowered:
                return track
        known = ", ".join(sorted(self.tracks)) or "none"
        raise ConfigError(f"{self.path}: no track named {name!r} (defined: {known})")

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

    def artifacts_path(self) -> Path | None:
        """Absolute path of the artifacts directory, resolved against the config file."""
        if self.artifacts_dir is None or self.artifacts_dir.is_absolute():
            return self.artifacts_dir
        return self.path.parent / self.artifacts_dir


def load(
    path: Path | str,
    *,
    tracks_dir: Path | str | None = None,
    control: Path | str | None = None,
    artifacts_dir: Path | str | None = None,
) -> Config:
    """Read and validate a config file. Fails fast with the file name in the message.

    The keyword overrides are the library caller's equivalent of the
    `TINA_*` environment variables the CLI honors: a program embedding Tina
    decides where its tracks, control file, and artifacts live under its own
    names, and passes the paths here. An override wins over both the file and
    the environment.
    """
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
    return parse(raw, path, tracks_dir=tracks_dir, control=control, artifacts_dir=artifacts_dir)


def parse(
    raw: dict[str, Any],
    path: Path | str = "<config>",
    *,
    tracks_dir: Path | str | None = None,
    control: Path | str | None = None,
    artifacts_dir: Path | str | None = None,
) -> Config:
    """Build a Config from an already-decoded TOML mapping. Overrides as in `load`."""
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
            "tracks_dir": tracks_dir
            or os.environ.get(TRACKS_DIR_VAR)
            or raw.get("tracks_dir", "tracks"),
            "control": control or raw.get("control"),
            "artifacts_dir": artifacts_dir
            or os.environ.get(ARTIFACTS_DIR_VAR)
            or raw.get("artifacts_dir"),
            "models": raw.get("models", []),
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
    """Both mismatch directions between {model} and the track key fail at load,
    and so does a model the deployment has not listed.

    A command referencing {model} with no track value would run the agent with
    the literal `{model}` as an argument; a track value under a command that
    never references it would silently not reach the harness; a track naming
    a model outside `models` would fail every run at minute one of the
    harness, with the error surfacing where nobody is looking.
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
        if track.model is not None and not config.allows_model(track.model):
            raise ConfigError(
                f"{config.path}: [{track.name}]: model {track.model!r} is not in `models`"
                f" (listed: {', '.join(config.models)})",
                fix="Add it to the top-level `models` list once the provider serves it here.",
            )
