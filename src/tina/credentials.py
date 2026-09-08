"""Short-lived credentials, without Tina implementing any scheme for them.

Production wants tokens minted per worker — GitHub App installation tokens,
workload identity — and those expire while a run is still going. Tina cannot
know how to mint one, and should not (#32 §15): the agent's own tools need the
same credential, so the mint has to live outside Tina anyway, typically a
credential helper the image already ships.

What Tina can do is run it. A `*_TOKEN_COMMAND` variable names a command whose
stdout is the current token. The command is run once to start and again when
the tracker answers 401 — a token that has aged out — after which the request
is retried once. A deployment with long-lived tokens sets neither variable and
nothing here runs.
"""

from __future__ import annotations

import os
import shlex
import subprocess

from tina.errors import TinaError
from tina.log import get_logger

log = get_logger(__name__)

#: A command whose stdout is the current GitHub token, for deployments that
#: mint short-lived ones. Takes precedence over GITHUB_TOKEN / GH_TOKEN.
GITHUB_TOKEN_COMMAND = "GITHUB_TOKEN_COMMAND"

TIMEOUT = 60.0


class CredentialError(TinaError, RuntimeError):
    """A token command could not produce a token."""


def token_command(var: str) -> list[str] | None:
    """The configured command, split like a shell would, or None when unset."""
    raw = os.environ.get(var, "")
    return shlex.split(raw) if raw.strip() else None


def run_token_command(command: list[str], var: str) -> str:
    """Run the command and return its stdout, stripped. Loud on any failure:
    a mint that fails is a deployment fault, and the request that needed the
    token would otherwise fail with a less useful 401."""
    try:
        completed = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=TIMEOUT
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CredentialError(
            f"{var}: could not run {shlex.join(command)}: {exc}",
            fix="Check the command is on PATH and answers within a minute.",
        ) from exc
    token = completed.stdout.strip()
    if completed.returncode != 0 or not token:
        detail = completed.stderr.strip()[:300] or f"exit {completed.returncode}, no output"
        raise CredentialError(f"{var}: {shlex.join(command)} produced no token: {detail}")
    log.info("token refreshed", extra={"source": var})
    return token
