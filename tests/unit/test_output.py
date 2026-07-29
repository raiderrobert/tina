from __future__ import annotations

import re

import pytest

from tina import output
from tina.errors import TinaError


def plain(text: str) -> str:
    """Rendered output with ANSI styling stripped.

    Styling is decorative — CI sets `GITHUB_ACTIONS`, which makes it appear
    there but not locally — so every assertion reads the text, not the escapes.
    """
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_message_only_is_one_line(capsys: pytest.CaptureFixture[str]) -> None:
    output.error("boom")

    err = plain(capsys.readouterr().err)
    assert err == "✗ boom\n"
    assert "Cause:" not in err
    assert "Fix:" not in err


def test_cause_is_labelled(capsys: pytest.CaptureFixture[str]) -> None:
    output.error("boom", cause="the file was empty")

    err = plain(capsys.readouterr().err)
    assert "✗ boom" in err
    assert "  Cause: the file was empty" in err
    assert "Fix:" not in err


def test_fix_is_labelled(capsys: pytest.CaptureFixture[str]) -> None:
    output.error("boom", fix="Set JIRA_API_TOKEN in the worker environment.")

    err = plain(capsys.readouterr().err)
    assert "✗ boom" in err
    assert "  Fix:   Set JIRA_API_TOKEN in the worker environment." in err
    assert "Cause:" not in err


def test_all_three_render_in_order(capsys: pytest.CaptureFixture[str]) -> None:
    output.error("boom", cause="the file was empty", fix="Write something to it.")

    lines = plain(capsys.readouterr().err).splitlines()
    assert lines == [
        "✗ boom",
        "  Cause: the file was empty",
        "  Fix:   Write something to it.",
    ]


def test_nothing_goes_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """stdout stays machine-readable: this module writes to stderr only."""
    output.error("boom", cause="c", fix="f")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err != ""


def test_a_bare_tina_error_renders_a_single_line(capsys: pytest.CaptureFixture[str]) -> None:
    """An error with no useful remedy is one line, not a label with nothing after it."""
    exc = TinaError("boom")

    output.error(str(exc), exc.cause, exc.fix)

    err = plain(capsys.readouterr().err)
    assert err.splitlines() == ["✗ boom"]


def test_a_dry_run_preview_renders_header_lines_summary_and_footer(
    capsys: pytest.CaptureFixture[str],
) -> None:
    output.dry_run_header()
    output.would("Would enqueue VUL-1 via local — CVE-2024-0001 in libfoo")
    output.would("Would enqueue VUL-2 via local — Bump libbar to 2.0.1")
    output.dry_run_footer("2 items matched (limit 5).")

    assert plain(capsys.readouterr().err).splitlines() == [
        "Dry run — no workers will be enqueued",
        "",
        "  Would enqueue VUL-1 via local — CVE-2024-0001 in libfoo",
        "  Would enqueue VUL-2 via local — Bump libbar to 2.0.1",
        "",
        "2 items matched (limit 5).",
        "",
        "Run without --dry-run to enqueue.",
    ]


def test_the_dry_run_footer_omits_an_empty_summary(capsys: pytest.CaptureFixture[str]) -> None:
    """No tally rather than a blank one, the same way `error()` drops an empty Cause."""
    output.dry_run_footer()

    assert plain(capsys.readouterr().err).splitlines() == [
        "",
        "Run without --dry-run to enqueue.",
    ]


def test_the_dry_run_preview_goes_to_stderr_only(capsys: pytest.CaptureFixture[str]) -> None:
    """stdout stays machine-readable: this module writes to stderr only."""
    output.dry_run_header()
    output.would("Would enqueue VUL-1 via local")
    output.dry_run_footer("1 items matched (limit 1).")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err != ""


def test_the_dry_run_frame_takes_the_run_preview_text(capsys: pytest.CaptureFixture[str]) -> None:
    """`tina run --dry-run` skips different things and offers a different next step."""
    output.dry_run_header("nothing will be claimed and no agent will run")
    output.would("Would claim VUL-1 — unassigned")
    output.dry_run_footer(action="claim VUL-1 and run the agent")

    assert plain(capsys.readouterr().err).splitlines() == [
        "Dry run — nothing will be claimed and no agent will run",
        "",
        "  Would claim VUL-1 — unassigned",
        "",
        "Run without --dry-run to claim VUL-1 and run the agent.",
    ]


def test_the_dry_run_frame_defaults_to_the_dispatch_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """dispatch passes neither parameter, so its preview is unchanged to the byte."""
    output.dry_run_header()
    output.dry_run_footer()

    assert plain(capsys.readouterr().err).splitlines() == [
        "Dry run — no workers will be enqueued",
        "",
        "",
        "Run without --dry-run to enqueue.",
    ]
