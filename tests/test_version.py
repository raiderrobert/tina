"""The version is derived, not written down twice — these tests keep it that way."""

from __future__ import annotations

import importlib
import importlib.metadata
import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import pytest

import tina

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def read_pyproject() -> dict[str, Any]:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)


def test_dist_name_matches_pyproject_project_name() -> None:
    """A rename that misses DIST_NAME would silently reduce `__version__` to the
    fallback sentinel. Fail loudly instead."""
    declared = read_pyproject()["project"]["name"]
    assert declared == tina.DIST_NAME


def test_version_matches_pyproject_when_installed() -> None:
    try:
        installed = version(tina.DIST_NAME)
    except PackageNotFoundError:
        pytest.skip(f"{tina.DIST_NAME} is not installed; __version__ is the fallback")
    expected = read_pyproject()["project"]["version"]
    assert installed == expected
    assert tina.__version__ == expected


def test_version_is_exported() -> None:
    assert "__version__" in tina.__all__


def test_import_falls_back_when_metadata_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def not_found(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", not_found)
    try:
        reloaded = importlib.reload(tina)
        assert reloaded.__version__ == tina.FALLBACK_VERSION
    finally:
        monkeypatch.undo()
        importlib.reload(tina)
