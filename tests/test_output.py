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
