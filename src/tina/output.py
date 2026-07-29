"""Human-facing output. The other half of the boundary from `tina.log`.

`tina.log` owns stdout: one JSON object per line, for whatever is collecting run
records. This module owns stderr: prose for the person who ran the command. The
two never overlap, and styling here is decorative only — every line reads the
same with ANSI stripped.
"""

from __future__ import annotations

import typer

ERROR = typer.colors.RED
DIM = typer.colors.BRIGHT_BLACK


def error(message: str, cause: str = "", fix: str = "") -> None:
    """Render a failure on stderr as `✗ message` plus what is known about it.

    `Cause:`/`Fix:` are omitted entirely when empty, so an error with no useful
    remedy is a single line rather than a label with nothing after it.
    """
    typer.echo(typer.style("✗ ", fg=ERROR) + message, err=True)
    if cause:
        typer.echo(typer.style("  Cause: ", fg=DIM) + cause, err=True)
    if fix:
        typer.echo(typer.style("  Fix:   ", fg=DIM) + fix, err=True)
