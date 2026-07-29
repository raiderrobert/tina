from __future__ import annotations

from pathlib import Path

import pytest

from tina import config

MINIMAL = """
harness = "pi"

[harnesses.pi]
command = ["pi", "--prompt-file", "{prompt_file}"]

[vul]
source = "jira"
query = "project = VUL"
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "tina.toml"
    path.write_text(text)
    return path


def test_defaults(tmp_path: Path) -> None:
    cfg = config.load(write(tmp_path, MINIMAL))

    assert cfg.executor == "local"
    assert cfg.tracks_dir == Path("tracks")
    assert cfg.track("vul").track == "vul", "track defaults to the table key"
    assert cfg.track("vul").result is None
    assert cfg.harness_config().command.args[0] == "pi"


def test_explicit_values_and_track_dir_resolves_against_config(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        MINIMAL.replace('harness = "pi"', 'harness = "pi"\ntracks_dir = "skills"', 1)
        + '\ntrack = "remediate"\nresult = "github:pr"\n',
    )
    cfg = config.load(path)

    assert cfg.track_dir(cfg.track("vul")) == tmp_path / "skills" / "remediate"
    assert cfg.track("vul").result == "github:pr"


def test_unknown_key_in_a_track_fails_fast(tmp_path: Path) -> None:
    with pytest.raises(config.ConfigError, match="jql"):
        config.load(write(tmp_path, MINIMAL + '\njql = "project = VUL"\n'))


def test_github_track_requires_repo(tmp_path: Path) -> None:
    text = MINIMAL.replace('source = "jira"', 'source = "github"')
    with pytest.raises(config.ConfigError, match="requires repo"):
        config.load(write(tmp_path, text))


def test_unknown_source_fails_fast(tmp_path: Path) -> None:
    text = MINIMAL.replace('source = "jira"', 'source = "linear"')
    with pytest.raises(config.ConfigError, match="jira"):
        config.load(write(tmp_path, text))


def test_unknown_executor_fails_fast(tmp_path: Path) -> None:
    text = MINIMAL.replace('harness = "pi"', 'harness = "pi"\nexecutor = "nomad"', 1)
    with pytest.raises(config.ConfigError, match="unknown executor 'nomad'"):
        config.load(write(tmp_path, text))


def test_unknown_harness_fails_fast(tmp_path: Path) -> None:
    text = MINIMAL.replace('harness = "pi"', 'harness = "gemini"', 1)
    with pytest.raises(config.ConfigError, match="harness 'gemini' has no"):
        config.load(write(tmp_path, text))


def test_missing_harness_key(tmp_path: Path) -> None:
    with pytest.raises(config.ConfigError, match="missing required top-level key 'harness'"):
        config.load(write(tmp_path, "[vul]\nsource = 'jira'\nquery = 'x'\n"))


def test_missing_query_names_the_track(tmp_path: Path) -> None:
    text = MINIMAL.replace('query = "project = VUL"', "")
    with pytest.raises(config.ConfigError, match=r"\[vul\]"):
        config.load(write(tmp_path, text))


def test_unknown_track_lists_the_known_ones(tmp_path: Path) -> None:
    cfg = config.load(write(tmp_path, MINIMAL))
    with pytest.raises(config.ConfigError, match="defined: vul"):
        cfg.track("bug")


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(config.ConfigError, match="not found"):
        config.load(tmp_path / "nope.toml")


def test_scalar_and_table_collision_gets_a_hint(tmp_path: Path) -> None:
    """`harness = "pi"` plus `[harness.pi]` is invalid TOML; say what to do instead."""
    text = 'harness = "pi"\n\n[harness.pi]\ncommand = ["pi"]\n'
    with pytest.raises(config.ConfigError) as excinfo:
        config.load(write(tmp_path, text))

    assert "invalid TOML" in str(excinfo.value)
    assert "[harnesses.<name>]" in excinfo.value.fix


def test_cloudrun_options_are_kept(tmp_path: Path) -> None:
    text = (
        MINIMAL.replace(
            'harness = "pi"',
            'harness = "pi"\nexecutor = "cloudrun"',
            1,
        )
        + '\n[executors.cloudrun]\nproject = "p"\nregion = "r"\njob = "j"\n'
    )
    cfg = config.load(write(tmp_path, text))

    assert cfg.cloudrun_options().job_path() == "projects/p/locations/r/jobs/j"


def test_example_config_is_valid() -> None:
    cfg = config.load(Path(__file__).parent.parent.parent / "examples" / "tina.toml")

    assert sorted(cfg.tracks) == ["bug", "vul"]
    assert cfg.track("bug").repo == "acme/api"


def test_argv_template_renders_both_placeholders(tmp_path: Path) -> None:
    template = config.ArgvTemplate(
        args=["agent", "--prompt", "{prompt_file}", "--out={outcome_dir}"]
    )

    rendered = template.render(tmp_path / "prompt.md", tmp_path)

    assert rendered == ["agent", "--prompt", str(tmp_path / "prompt.md"), f"--out={tmp_path}"]


def test_a_typo_in_a_placeholder_is_not_passed_through(tmp_path: Path) -> None:
    """Left unchecked this reaches the agent as a literal argument."""
    text = MINIMAL.replace("{prompt_file}", "{prompt-file}")
    with pytest.raises(config.ConfigError, match="unknown placeholder"):
        config.load(write(tmp_path, text))


def test_an_unknown_placeholder_names_what_is_supported(tmp_path: Path) -> None:
    text = MINIMAL.replace("{prompt_file}", "{workdir}")
    with pytest.raises(config.ConfigError, match=r"\{outcome_dir\}, \{prompt_file\}"):
        config.load(write(tmp_path, text))


def test_a_command_without_the_prompt_is_rejected(tmp_path: Path) -> None:
    """An agent invoked without the prompt file was never given the work."""
    text = MINIMAL.replace('"--prompt-file", "{prompt_file}"', '"--resume"')
    with pytest.raises(config.ConfigError, match="prompt_file"):
        config.load(write(tmp_path, text))


def test_an_empty_command_is_rejected(tmp_path: Path) -> None:
    text = MINIMAL.replace('command = ["pi", "--prompt-file", "{prompt_file}"]', "command = []")
    with pytest.raises(config.ConfigError, match="command"):
        config.load(write(tmp_path, text))


def test_partial_cloudrun_options_fail_at_load(tmp_path: Path) -> None:
    """Not at dispatch time, when a scheduler is already depending on it."""
    text = MINIMAL + '\n[executors.cloudrun]\nproject = "p"\nregion = "r"\n'
    with pytest.raises(config.ConfigError, match="executors.cloudrun.job"):
        config.load(write(tmp_path, text))


def test_an_unknown_executor_table_is_rejected(tmp_path: Path) -> None:
    text = MINIMAL + '\n[executors.nomad]\ndatacenter = "dc1"\n'
    with pytest.raises(config.ConfigError, match="nomad"):
        config.load(write(tmp_path, text))
