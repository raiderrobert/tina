from __future__ import annotations

from tina.config import ConfigError
from tina.errors import TinaError
from tina.executors.base import ExecutorError
from tina.prompt import PromptError
from tina.sources.base import SourceError


def test_str_is_still_exactly_the_message() -> None:
    """The JSON log line's `message` field does not change shape."""
    exc = TinaError("boom")

    assert str(exc) == "boom"
    assert exc.cause == ""
    assert exc.fix == ""


def test_cause_and_fix_do_not_leak_into_str() -> None:
    exc = TinaError("boom", "the file was empty", "Write something to it.")

    assert str(exc) == "boom"
    assert exc.cause == "the file was empty"
    assert exc.fix == "Write something to it."


def test_dual_base_subclasses_still_take_one_argument() -> None:
    """The multiple-inheritance MRO accepts the new signature unchanged."""
    for subclass, builtin_base in (
        (ConfigError, ValueError),
        (PromptError, RuntimeError),
        (ExecutorError, RuntimeError),
        (SourceError, RuntimeError),
    ):
        exc = subclass("boom")

        assert str(exc) == "boom"
        assert exc.cause == ""
        assert exc.fix == ""
        assert isinstance(exc, builtin_base)
