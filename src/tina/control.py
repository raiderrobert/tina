"""Runtime policy: the gates a dispatch honors, resolved fresh every cycle.

`tina.toml` declares what exists and changes at deploy speed. The control file
is the policy tier (ADR-011): `paused` and `max_concurrency`, changed in
minutes by whoever holds the pager, without a deploy. It is a path Tina reads,
never an object Tina fetches (ADR-012), and it is read on every dispatch with
no cache — it is a kill switch, so staleness is the failure mode.

The file is untrusted input on a privileged surface, so the loader fails
closed: anything unreadable or invalid pauses the factory rather than running
it unthrottled. The one exception is a file that is simply not there, which
means "no control plane configured" and yields the defaults — a first deploy
must not brick the factory. `PermissionError` is deliberately not that case:
a broken mount must not look like a control plane that was never set up.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tina.log import get_logger

INLINE_VAR = "TINA_CONTROL_INLINE"
PATH_VAR = "TINA_CONTROL"

#: Hard bound on fan-out, applied in code after validation. The control file
#: can only lower concurrency; a fat-fingered 5000 hits this, not the executor.
MAX_CONCURRENCY_CEILING = 64

log = get_logger(__name__)


class Policy(BaseModel):
    """The control file's schema. Strict, so a bool is never quietly an int."""

    model_config = ConfigDict(extra="forbid", strict=True)

    paused: bool = False
    # None means no throttle: the caller's --limit stands alone.
    max_concurrency: int | None = Field(default=None, ge=0)


@dataclass(frozen=True)
class LoadedPolicy:
    """The effective policy plus where it came from, for the dispatch log."""

    paused: bool
    max_concurrency: int | None
    origin: str


DEFAULTS = LoadedPolicy(paused=False, max_concurrency=None, origin="defaults")


def load(configured_path: Path | None = None) -> LoadedPolicy:
    """Resolve the policy: inline env, then env path, then config key, then defaults.

    Logs the source consulted and the effective values whenever a control
    plane is configured at all, so a surprising throttle is diagnosable from
    stdout. With nothing configured there is nothing to say, and dispatch
    output stays exactly what it was before the control plane existed.
    """
    inline = os.environ.get(INLINE_VAR)
    env_path = os.environ.get(PATH_VAR)
    if inline:
        loaded = _parse(inline, INLINE_VAR)
    elif env_path:
        loaded = _read(Path(env_path), PATH_VAR)
    elif configured_path is not None:
        loaded = _read(configured_path, "control key")
    else:
        return DEFAULTS

    log.info(
        "control policy",
        extra={
            "origin": loaded.origin,
            "paused": loaded.paused,
            "max_concurrency": loaded.max_concurrency,
        },
    )
    return loaded


def _read(path: Path, origin: str) -> LoadedPolicy:
    """One file read, sorted into the fail-closed table.

    `FileNotFoundError` is caught before `OSError` because it is the one
    subclass that means defaults; every other read failure — permission
    denied, a directory, I/O — is a broken mount and pauses.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        log.info("control file absent; using defaults", extra={"path": str(path)})
        return LoadedPolicy(paused=False, max_concurrency=None, origin=origin)
    except (OSError, UnicodeDecodeError) as exc:
        return _fail_closed(origin, f"unreadable: {exc}")
    return _parse(text, origin)


def _parse(text: str, origin: str) -> LoadedPolicy:
    try:
        policy = Policy(**tomllib.loads(text))
    except (tomllib.TOMLDecodeError, ValidationError) as exc:
        return _fail_closed(origin, str(exc))
    return LoadedPolicy(
        paused=policy.paused,
        max_concurrency=_clamp(policy.max_concurrency),
        origin=origin,
    )


def _fail_closed(origin: str, problem: str) -> LoadedPolicy:
    log.warning(
        "control policy invalid; pausing dispatch",
        extra={"origin": origin, "problem": problem},
    )
    return LoadedPolicy(paused=True, max_concurrency=0, origin=origin)


def _clamp(value: int | None) -> int | None:
    if value is not None and value > MAX_CONCURRENCY_CEILING:
        log.warning(
            "max_concurrency clamped",
            extra={"requested": value, "ceiling": MAX_CONCURRENCY_CEILING},
        )
        return MAX_CONCURRENCY_CEILING
    return value
