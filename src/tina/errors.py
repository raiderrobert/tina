"""The one thing the CLI catches.

Every failure Tina is responsible for — a bad config, an unreachable tracker, a
missing track — is a `TinaError` and exits nonzero. An *agent* reporting
`failed` is not one of these: that outcome is data, and the run still exits 0.

The subclasses keep their historical builtin bases so existing `except
ValueError` / `except RuntimeError` call sites keep working.
"""

from __future__ import annotations


class TinaError(Exception):
    """A failure in Tina itself, as opposed to an outcome an agent reported.

    `message` says what broke and names the file or path involved; `cause` is
    the underlying detail when there is one; `fix` is the single action that
    resolves it. Only the CLI boundary renders them — see `tina.output`.
    """

    def __init__(self, message: str, cause: str = "", fix: str = "") -> None:
        super().__init__(message)
        self.cause = cause
        self.fix = fix
