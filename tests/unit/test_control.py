from __future__ import annotations

import logging
from pathlib import Path

import pytest

from tina import control

VALID = "paused = false\nmax_concurrency = 3\n"


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "control.toml"
    path.write_text(text)
    return path


# --- resolution order: inline env, path env, config key, defaults -----------


def test_no_control_plane_configured_means_defaults() -> None:
    policy = control.load()

    assert policy.paused is False
    assert policy.max_concurrency is None
    assert policy.origin == "defaults"


def test_inline_toml_wins_over_the_path_and_the_config_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TINA_CONTROL_INLINE", "max_concurrency = 2")
    monkeypatch.setenv("TINA_CONTROL", str(write(tmp_path, VALID)))

    policy = control.load(configured_path=tmp_path / "control.toml")

    assert policy.max_concurrency == 2
    assert policy.origin == "TINA_CONTROL_INLINE"


def test_the_env_path_wins_over_the_config_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TINA_CONTROL", str(write(tmp_path, "max_concurrency = 4")))
    other = tmp_path / "other.toml"
    other.write_text(VALID)

    policy = control.load(configured_path=other)

    assert policy.max_concurrency == 4
    assert policy.origin == "TINA_CONTROL"


def test_the_config_key_is_used_when_no_env_is_set(tmp_path: Path) -> None:
    policy = control.load(configured_path=write(tmp_path, "paused = true"))

    assert policy.paused is True
    assert policy.origin == "control key"


# --- the fail-closed table (ADR-011 I2) --------------------------------------


def test_an_absent_file_means_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A first deploy must not brick the factory."""
    monkeypatch.setenv("TINA_CONTROL", str(tmp_path / "missing.toml"))

    policy = control.load()

    assert policy.paused is False
    assert policy.max_concurrency is None


def test_malformed_toml_pauses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo'd emergency edit must not unleash the factory."""
    monkeypatch.setenv("TINA_CONTROL", str(write(tmp_path, "paused = ")))

    assert control.load().paused is True


def test_an_unknown_key_pauses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TINA_CONTROL", str(write(tmp_path, "pasued = true")))

    assert control.load().paused is True


def test_a_negative_max_concurrency_pauses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TINA_CONTROL", str(write(tmp_path, "max_concurrency = -1")))

    assert control.load().paused is True


def test_an_int_is_not_a_bool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TINA_CONTROL", str(write(tmp_path, "paused = 1")))

    assert control.load().paused is True


def test_a_bool_is_not_an_int(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TINA_CONTROL", str(write(tmp_path, "max_concurrency = true")))

    assert control.load().paused is True


def test_malformed_inline_toml_pauses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TINA_CONTROL_INLINE", "not toml at all")

    assert control.load().paused is True


def test_permission_denied_pauses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Not "no file": a broken mount must not look like "no control plane"."""
    path = write(tmp_path, VALID)
    path.chmod(0o000)
    monkeypatch.setenv("TINA_CONTROL", str(path))
    try:
        policy = control.load()
    finally:
        path.chmod(0o644)

    assert policy.paused is True


def test_any_other_read_error_pauses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A directory raises IsADirectoryError on read: fail closed, same as I/O."""
    monkeypatch.setenv("TINA_CONTROL", str(tmp_path))

    assert control.load().paused is True


def test_undecodable_bytes_pause(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "control.toml"
    path.write_bytes(b"\xff\xfe\x00")
    monkeypatch.setenv("TINA_CONTROL", str(path))

    assert control.load().paused is True


# --- the clamp: the control file is untrusted input --------------------------


def test_max_concurrency_at_the_ceiling_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = f"max_concurrency = {control.MAX_CONCURRENCY_CEILING}"
    monkeypatch.setenv("TINA_CONTROL", str(write(tmp_path, text)))

    policy = control.load()

    assert policy.max_concurrency == control.MAX_CONCURRENCY_CEILING
    assert policy.paused is False


def test_max_concurrency_above_the_ceiling_is_clamped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = f"max_concurrency = {control.MAX_CONCURRENCY_CEILING + 1}"
    monkeypatch.setenv("TINA_CONTROL", str(write(tmp_path, text)))

    policy = control.load()

    assert policy.max_concurrency == control.MAX_CONCURRENCY_CEILING
    assert policy.paused is False, "a fat-fingered value is clamped, not a pause"


def test_a_fat_fingered_5000_hits_the_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TINA_CONTROL", str(write(tmp_path, "max_concurrency = 5000")))

    assert control.load().max_concurrency == control.MAX_CONCURRENCY_CEILING


# --- diagnosability: source and effective values on stdout -------------------


def test_the_source_and_the_effective_values_are_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("TINA_CONTROL", str(write(tmp_path, VALID)))

    with caplog.at_level(logging.INFO):
        control.load()

    record = next(r for r in caplog.records if r.message == "control policy")
    assert record.__dict__["origin"] == "TINA_CONTROL"
    assert record.__dict__["paused"] is False
    assert record.__dict__["max_concurrency"] == 3


def test_a_fail_closed_pause_logs_the_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("TINA_CONTROL", str(write(tmp_path, "paused = ")))

    with caplog.at_level(logging.INFO):
        control.load()

    warning = next(r for r in caplog.records if r.levelno == logging.WARNING)
    assert "pausing" in warning.message
    assert warning.__dict__["origin"] == "TINA_CONTROL"


def test_defaults_log_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """No control plane configured is the quiet path: nothing new on stdout."""
    with caplog.at_level(logging.INFO):
        control.load()

    assert caplog.records == []
