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
DRY_RUN = typer.colors.YELLOW


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


def dry_run_header() -> None:
    """Open a preview. Nothing printed after this line changed anything."""
    typer.echo(typer.style("Dry run", fg=DRY_RUN) + " — no workers will be enqueued\n", err=True)


def would(message: str) -> None:
    """One thing a real run would have done, indented under the header."""
    typer.echo("  " + message, err=True)


def dry_run_footer(summary: str = "") -> None:
    """Close a preview, with an optional tally.

    `summary` is omitted when empty, the same way `error()` drops an empty
    `Cause:`/`Fix:` rather than printing a label with nothing after it.
    """
    typer.echo("", err=True)
    if summary:
        typer.echo(summary, err=True)
        typer.echo("", err=True)
    typer.echo(typer.style("Run without --dry-run to enqueue.", fg=DIM), err=True)
