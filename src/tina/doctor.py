"""Admission, live: prove the deployment works before the first run.

`tina validate` reads files. `tina doctor` talks to the systems: every source a
track uses authenticates and its query parses, the harness binary is on PATH,
the executor can be constructed, and the control file — if one is configured
— loads without failing closed. The first-run failure mode for a new user is
otherwise a stack trace from whichever adapter happened to be called first,
after they have already written a config and a track.

Every check is read-only. Nothing is claimed, enqueued, or run.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from tina import control, executors, sources
from tina.config import Config, ConfigError
from tina.config import load as load_config
from tina.errors import TinaError
from tina.sources.base import Source


@dataclass(frozen=True)
class Check:
    """One probe's verdict. `detail` says what was found, either way."""

    name: str
    ok: bool
    detail: str = ""


SourceBuilder = Callable[..., Source]


def diagnose(
    config_path: Path | str,
    only: str | None = None,
    build_source: SourceBuilder | None = None,
) -> list[Check]:
    """Run every probe against the config and return the verdicts in order.

    `build_source` is the seam for tests: the real one, `sources.build`, opens
    an HTTP client from the environment. Resolved at call time so a patched
    `sources.build` is honored.
    """
    build_source = build_source or sources.build
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        return [Check("config loads", False, str(exc))]
    checks = [Check("config loads", True, str(config.path))]
    checks.extend(_harness(config))
    checks.append(_executor(config))
    checks.append(_control(config))
    tracks = config.tracks.values()
    if only is not None:
        try:
            tracks = [config.track(only)]
        except ConfigError as exc:
            return [*checks, Check(f"track {only!r}", False, str(exc))]
    for track in tracks:
        checks.extend(_track(config, track.name, build_source))
    return checks


def _harness(config: Config) -> Iterator[Check]:
    harness = config.harness_config()
    binary = harness.command.args[0]
    found = shutil.which(binary)
    yield Check(
        f"harness {harness.name!r} on PATH",
        found is not None,
        found or f"{binary!r} not found; the worker image must install it",
    )


def _executor(config: Config) -> Check:
    try:
        executor = executors.build(config)
    except TinaError as exc:
        return Check(f"executor {config.executor!r}", False, f"{exc} {exc.fix}".strip())
    return Check(f"executor {config.executor!r}", True, type(executor).__name__)


def _control(config: Config) -> Check:
    policy = control.load(config.control_path())
    if policy.origin == "defaults":
        return Check("control policy", True, "none configured; defaults apply")
    if policy.paused and policy.max_concurrency == 0:
        return Check("control policy", False, f"{policy.origin}: invalid, failing closed (paused)")
    throttle = "unset" if policy.max_concurrency is None else policy.max_concurrency
    return Check(
        "control policy",
        True,
        f"{policy.origin}: paused {str(policy.paused).lower()}, max_concurrency {throttle}",
    )


def _track(config: Config, name: str, build_source: SourceBuilder) -> Iterator[Check]:
    track = config.track(name)
    skill = config.track_dir(track) / "SKILL.md"
    yield Check(f"[{name}] skill", skill.is_file(), str(skill))
    if track.mode == "sweep":
        return
    try:
        source = build_source(track)
        identity = source.login()
    except TinaError as exc:
        yield Check(f"[{name}] {track.source} credentials", False, f"{exc} {exc.fix}".strip())
        return
    yield Check(f"[{name}] {track.source} credentials", True, f"acting as {identity}")
    try:
        matched = len(source.query(track.query))
    except TinaError as exc:
        yield Check(f"[{name}] query", False, str(exc))
        return
    yield Check(f"[{name}] query", True, f"{matched} item(s) match now")
