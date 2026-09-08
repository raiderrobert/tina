from __future__ import annotations

from pathlib import Path

import pytest

from tina import config
from tina.query import Query
from tina.sources import render

MINIMAL = """
harness = "pi"

[harnesses.pi]
command = ["pi", "--prompt-file", "{prompt_file}"]

[vul]
source = "jira"
project = "VUL"
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


def test_control_defaults_to_none(tmp_path: Path) -> None:
    cfg = config.load(write(tmp_path, MINIMAL))

    assert cfg.control is None
    assert cfg.control_path() is None


def test_a_relative_control_path_resolves_against_the_config_file(tmp_path: Path) -> None:
    text = MINIMAL.replace('harness = "pi"', 'harness = "pi"\ncontrol = "control.toml"', 1)
    cfg = config.load(write(tmp_path, text))

    assert cfg.control_path() == tmp_path / "control.toml"


def test_an_absolute_control_path_is_kept(tmp_path: Path) -> None:
    text = MINIMAL.replace(
        'harness = "pi"', 'harness = "pi"\ncontrol = "/mnt/config/control.toml"', 1
    )
    cfg = config.load(write(tmp_path, text))

    assert cfg.control_path() == Path("/mnt/config/control.toml")


def test_enabled_defaults_to_true(tmp_path: Path) -> None:
    """A track is on by virtue of being present."""
    cfg = config.load(write(tmp_path, MINIMAL))

    assert cfg.track("vul").enabled is True


def test_enabled_false_is_kept(tmp_path: Path) -> None:
    cfg = config.load(write(tmp_path, MINIMAL + "\nenabled = false\n"))

    assert cfg.track("vul").enabled is False


def test_a_disabled_track_is_still_fully_validated(tmp_path: Path) -> None:
    """Disabling a track must not let it rot: unknown keys still fail fast."""
    with pytest.raises(config.ConfigError, match="jql"):
        config.load(write(tmp_path, MINIMAL + '\nenabled = false\njql = "project = VUL"\n'))


def test_a_disabled_github_track_still_requires_repo(tmp_path: Path) -> None:
    text = (
        MINIMAL.replace('source = "jira"\nproject = "VUL"', 'source = "github"')
        + "\nenabled = false\n"
    )
    with pytest.raises(config.ConfigError, match="requires repo"):
        config.load(write(tmp_path, text))


def test_on_failure_defaults_to_leave(tmp_path: Path) -> None:
    """The conservative choice: annotating writes to the tracker unasked."""
    cfg = config.load(write(tmp_path, MINIMAL))

    assert cfg.track("vul").on_failure == "leave"
    assert cfg.track("vul").blocked_label == "tina-blocked"


def test_on_failure_annotate_is_kept(tmp_path: Path) -> None:
    cfg = config.load(write(tmp_path, MINIMAL + '\non_failure = "annotate"\n'))

    assert cfg.track("vul").on_failure == "annotate"


def test_an_unknown_on_failure_value_fails_fast(tmp_path: Path) -> None:
    with pytest.raises(config.ConfigError, match="on_failure"):
        config.load(write(tmp_path, MINIMAL + '\non_failure = "retry"\n'))


def test_an_empty_blocked_label_fails_fast(tmp_path: Path) -> None:
    with pytest.raises(config.ConfigError, match="blocked_label"):
        config.load(write(tmp_path, MINIMAL + '\nblocked_label = ""\n'))


MODEL_COMMAND = MINIMAL.replace(
    'command = ["pi", "--prompt-file", "{prompt_file}"]',
    'command = ["pi", "--prompt-file", "{prompt_file}", "--model", "{model}"]',
)


def test_model_defaults_to_none(tmp_path: Path) -> None:
    cfg = config.load(write(tmp_path, MINIMAL))

    assert cfg.track("vul").model is None


def test_model_is_kept(tmp_path: Path) -> None:
    cfg = config.load(write(tmp_path, MODEL_COMMAND + '\nmodel = "claude-sonnet-x"\n'))

    assert cfg.track("vul").model == "claude-sonnet-x"


def test_a_model_command_requires_model_on_every_track(tmp_path: Path) -> None:
    """The named track is the one missing the key, not the first one."""
    text = (
        MODEL_COMMAND
        + '\nmodel = "claude-sonnet-x"\n'
        + '\n[bug]\nsource = "jira"\nproject = "BUGS"\n'
    )
    with pytest.raises(config.ConfigError, match=r"\[bug\].*\{model\}"):
        config.load(write(tmp_path, text))


def test_model_without_a_model_command_fails_at_load(tmp_path: Path) -> None:
    """The value would silently never reach the harness."""
    with pytest.raises(config.ConfigError, match=r"\[vul\].*\{model\}"):
        config.load(write(tmp_path, MINIMAL + '\nmodel = "claude-sonnet-x"\n'))


@pytest.mark.parametrize("value", ["", "two words", "tab\there", " padded"])
def test_a_malformed_model_value_fails_at_load(tmp_path: Path, value: str) -> None:
    """Shape only — whether the model exists is the deployment's problem."""
    with pytest.raises(config.ConfigError, match="model"):
        config.load(write(tmp_path, MODEL_COMMAND + f'\nmodel = "{value}"\n'))


def test_a_model_command_on_an_unselected_harness_needs_nothing(tmp_path: Path) -> None:
    """Only the selected harness's command binds the tracks."""
    text = MINIMAL + '\n[harnesses.claude]\ncommand = ["claude", "{prompt_file}", "{model}"]\n'
    cfg = config.load(write(tmp_path, text))

    assert cfg.track("vul").model is None


def test_argv_template_renders_the_model(tmp_path: Path) -> None:
    template = config.ArgvTemplate(args=["agent", "{prompt_file}", "--model", "{model}"])

    rendered = template.render(tmp_path / "prompt.md", tmp_path, model="claude-sonnet-x")

    assert rendered == ["agent", str(tmp_path / "prompt.md"), "--model", "claude-sonnet-x"]


def test_claim_defaults_to_assign(tmp_path: Path) -> None:
    """Today's behavior, unless a track opts out."""
    cfg = config.load(write(tmp_path, MINIMAL))

    assert cfg.track("vul").claim == "assign"
    assert cfg.track("vul").claim_label is None
    assert cfg.track("vul").claim_transition is None


def test_claim_label_and_transition_are_kept(tmp_path: Path) -> None:
    text = MINIMAL + '\nclaim = "label"\nclaim_label = "bot-claimed"\n'
    cfg = config.load(write(tmp_path, text))

    assert cfg.track("vul").claim == "label"
    assert cfg.track("vul").claim_label == "bot-claimed"


def test_an_unknown_claim_value_fails_fast(tmp_path: Path) -> None:
    with pytest.raises(config.ConfigError, match=r"\[vul\].*claim"):
        config.load(write(tmp_path, MINIMAL + '\nclaim = "steal"\n'))


def test_claim_label_requires_the_label_strategy(tmp_path: Path) -> None:
    """Set but unused, the label would silently never be applied."""
    with pytest.raises(config.ConfigError, match=r"\[vul\].*claim_label"):
        config.load(write(tmp_path, MINIMAL + '\nclaim_label = "bot-claimed"\n'))


def test_the_label_strategy_requires_claim_label(tmp_path: Path) -> None:
    with pytest.raises(config.ConfigError, match=r"\[vul\].*claim_label"):
        config.load(write(tmp_path, MINIMAL + '\nclaim = "label"\n'))


def test_claim_transition_is_kept_on_a_jira_track(tmp_path: Path) -> None:
    cfg = config.load(write(tmp_path, MINIMAL + '\nclaim_transition = "In Progress"\n'))

    assert cfg.track("vul").claim_transition == "In Progress"


def test_claim_transition_on_a_github_track_fails_fast(tmp_path: Path) -> None:
    """GitHub Issues has no workflow to transition."""
    text = (
        MINIMAL.replace('source = "jira"\nproject = "VUL"', 'source = "github"\nrepo = "acme/api"')
        + '\nclaim_transition = "In Progress"\n'
    )
    with pytest.raises(config.ConfigError, match=r"\[vul\].*claim_transition"):
        config.load(write(tmp_path, text))


def test_claim_transition_under_claim_none_fails_fast(tmp_path: Path) -> None:
    """No claim ever succeeds, so the transition would silently never fire."""
    text = MINIMAL + '\nclaim = "none"\nclaim_transition = "In Progress"\n'
    with pytest.raises(config.ConfigError, match=r"\[vul\].*claim_transition"):
        config.load(write(tmp_path, text))


SWEEP = """
harness = "pi"

[harnesses.pi]
command = ["pi", "--prompt-file", "{prompt_file}"]

[reap]
mode = "sweep"
"""


def test_mode_defaults_to_queue(tmp_path: Path) -> None:
    cfg = config.load(write(tmp_path, MINIMAL))

    assert cfg.track("vul").mode == "queue"


def test_a_sweep_track_needs_no_source_or_query(tmp_path: Path) -> None:
    cfg = config.load(write(tmp_path, SWEEP))

    assert cfg.track("reap").mode == "sweep"
    assert cfg.track("reap").source is None


def test_an_unknown_mode_fails_fast(tmp_path: Path) -> None:
    with pytest.raises(config.ConfigError, match=r"\[reap\].*mode"):
        config.load(write(tmp_path, SWEEP.replace('"sweep"', '"cron"')))


@pytest.mark.parametrize(
    "key",
    [
        'source = "jira"',
        'project = "VUL"',
        'repo = "acme/api"',
        'claim = "none"',
        'claim_label = "bot-claimed"',
        'claim_transition = "In Progress"',
        'on_failure = "annotate"',
        'blocked_label = "tina-blocked"',
    ],
)
def test_a_queue_key_on_a_sweep_entry_fails_at_load(tmp_path: Path, key: str) -> None:
    """Each stray key is named, even one set to its default value."""
    name = key.split(" ")[0]
    with pytest.raises(config.ConfigError, match=rf"\[reap\].*{name}"):
        config.load(write(tmp_path, SWEEP + key + "\n"))


def test_every_stray_queue_key_is_named_together(tmp_path: Path) -> None:
    text = SWEEP + 'source = "jira"\nproject = "VUL"\non_failure = "annotate"\n'
    with pytest.raises(config.ConfigError, match="on_failure, project, source"):
        config.load(write(tmp_path, text))


def test_a_sweep_track_keeps_enabled_model_and_env(tmp_path: Path) -> None:
    text = (
        SWEEP.replace(
            'command = ["pi", "--prompt-file", "{prompt_file}"]',
            'command = ["pi", "--prompt-file", "{prompt_file}", "--model", "{model}"]',
        )
        + 'enabled = false\nmodel = "claude-haiku-x"\n\n[reap.env]\nREAPER_DRY = "1"\n'
    )
    cfg = config.load(write(tmp_path, text))

    assert cfg.track("reap").enabled is False
    assert cfg.track("reap").model == "claude-haiku-x"
    assert cfg.track("reap").env == {"REAPER_DRY": "1"}


def test_a_queue_track_without_a_source_fails_at_load(tmp_path: Path) -> None:
    text = MINIMAL.replace('source = "jira"\n', "")
    with pytest.raises(config.ConfigError, match=r"\[vul\].*source"):
        config.load(write(tmp_path, text))


def test_env_defaults_to_empty(tmp_path: Path) -> None:
    """Tracks without the table are unaffected."""
    cfg = config.load(write(tmp_path, MINIMAL))

    assert cfg.track("vul").env == {}


def test_env_values_are_kept(tmp_path: Path) -> None:
    text = MINIMAL + '\n[vul.env]\nTRIAGED_LABEL = "bot-triaged"\nBOT_LOGINS = "a,b"\n'
    cfg = config.load(write(tmp_path, text))

    assert cfg.track("vul").env == {
        "TRIAGED_LABEL": "bot-triaged",
        "BOT_LOGINS": "a,b",
    }


@pytest.mark.parametrize("name", ["lower_case", "1LEADING_DIGIT", "_UNDERSCORE", "WITH-DASH"])
def test_a_malformed_env_name_fails_at_load(tmp_path: Path, name: str) -> None:
    """Uppercase alphanumerics and underscores, starting with a letter."""
    text = MINIMAL + f'\n[vul.env]\n"{name}" = "x"\n'
    with pytest.raises(config.ConfigError, match=rf"\[vul\].*{name}"):
        config.load(write(tmp_path, text))


def test_an_env_name_in_tinas_namespace_fails_at_load(tmp_path: Path) -> None:
    """Colliding with a variable tina itself owns would change its behavior."""
    text = MINIMAL + '\n[vul.env]\nTINA_CONTROL = "/tmp/x"\n'
    with pytest.raises(config.ConfigError, match=r"\[vul\].*TINA_CONTROL"):
        config.load(write(tmp_path, text))


def test_a_non_string_env_value_fails_at_load(tmp_path: Path) -> None:
    """Values are literal strings, never coerced."""
    text = MINIMAL + "\n[vul.env]\nBOT_LIMIT = 5\n"
    with pytest.raises(config.ConfigError, match=r"\[vul\].*BOT_LIMIT"):
        config.load(write(tmp_path, text))


def test_unknown_key_in_a_track_fails_fast(tmp_path: Path) -> None:
    with pytest.raises(config.ConfigError, match="jql"):
        config.load(write(tmp_path, MINIMAL + '\njql = "project = VUL"\n'))


def test_github_track_requires_repo(tmp_path: Path) -> None:
    text = MINIMAL.replace('source = "jira"\nproject = "VUL"', 'source = "github"')
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
        config.load(write(tmp_path, "[vul]\nsource = 'jira'\nproject = 'X'\n"))


def test_missing_project_names_the_track(tmp_path: Path) -> None:
    text = MINIMAL.replace('project = "VUL"', "")
    with pytest.raises(config.ConfigError, match=r"\[vul\].*requires project"):
        config.load(write(tmp_path, text))


def test_a_raw_query_key_points_at_the_parts(tmp_path: Path) -> None:
    """The removed key gets a pointer, not \"extra inputs are not permitted\"."""
    with pytest.raises(config.ConfigError, match="query is not a key; give the parts"):
        config.load(write(tmp_path, MINIMAL + '\nquery = "project = VUL"\n'))


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

    assert sorted(cfg.tracks) == ["bug", "stuck-claims", "vul"]
    assert cfg.track("stuck-claims").mode == "sweep"
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
    with pytest.raises(
        config.ConfigError,
        match=r"\{model\}, \{outcome_dir\}, \{prompt_file\}, \{session_dir\}",
    ):
        config.load(write(tmp_path, text))


def test_argv_template_renders_the_session_dir(tmp_path: Path) -> None:
    template = config.ArgvTemplate(args=["agent", "{prompt_file}", "--session={session_dir}"])

    rendered = template.render(tmp_path / "prompt.md", tmp_path, session_dir=tmp_path / "session")

    assert rendered == [
        "agent",
        str(tmp_path / "prompt.md"),
        f"--session={tmp_path / 'session'}",
    ]


def test_artifacts_dir_defaults_to_none(tmp_path: Path) -> None:
    cfg = config.load(write(tmp_path, MINIMAL))

    assert cfg.artifacts_dir is None
    assert cfg.artifacts_path() is None


def test_a_relative_artifacts_dir_resolves_against_the_config_file(tmp_path: Path) -> None:
    text = MINIMAL.replace('harness = "pi"', 'harness = "pi"\nartifacts_dir = "artifacts"', 1)
    cfg = config.load(write(tmp_path, text))

    assert cfg.artifacts_path() == tmp_path / "artifacts"


def test_an_absolute_artifacts_dir_is_kept(tmp_path: Path) -> None:
    text = MINIMAL.replace('harness = "pi"', 'harness = "pi"\nartifacts_dir = "/mnt/artifacts"', 1)
    cfg = config.load(write(tmp_path, text))

    assert cfg.artifacts_path() == Path("/mnt/artifacts")


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


# --- query parts -> tina.query.Query, rendered per source --------------------

JIRA_PARTS = """
harness = "pi"

[harnesses.pi]
command = ["pi", "--prompt-file", "{prompt_file}"]

[vul]
source = "jira"
project = "VUL"
extra = "labels not in (wontfix)"

[vul.filters]
Team = ["Payments", "Search"]
"""


def test_jira_parts_build_the_query(tmp_path: Path) -> None:
    cfg = config.load(write(tmp_path, JIRA_PARTS))

    assert cfg.track("vul").query == Query(
        scope="VUL",
        fields={"Team": ("Payments", "Search")},
        labels_none=("tina-blocked",),
        extra="labels not in (wontfix)",
    )
    assert render(cfg.track("vul")) == (
        'project = VUL AND status = "Open" AND "Team" in ("Payments", "Search")'
        ' AND assignee IS EMPTY AND (labels IS EMPTY OR labels not in ("tina-blocked"))'
        " AND (labels not in (wontfix)) ORDER BY created ASC"
    )


def test_a_blocked_transition_drops_the_label_guard_from_the_built_query(tmp_path: Path) -> None:
    text = JIRA_PARTS.replace('project = "VUL"', 'project = "VUL"\nblocked_transition = "Blocked"')
    cfg = config.load(write(tmp_path, text))

    assert cfg.track("vul").query.labels_none == ()
    assert cfg.track("vul").query.extra == "labels not in (wontfix)", "extra still applies"
    assert cfg.track("vul").blocked_transition == "Blocked"


def test_the_query_is_not_a_config_key(tmp_path: Path) -> None:
    """Nothing in the file spells it, so the options listing must not offer it."""
    assert "query" not in config.TrackConfig.model_fields


def test_github_structured_inputs_build_the_query(tmp_path: Path) -> None:
    text = """
harness = "pi"

[harnesses.pi]
command = ["pi", "--prompt-file", "{prompt_file}"]

[smoke]
source = "github"
repo = "acme/api"
labels = ["needs-triage"]
claim = "label"
claim_label = "bot-claimed"
"""
    cfg = config.load(write(tmp_path, text))

    assert cfg.track("smoke").query == Query(
        scope="acme/api",
        labels_all=("needs-triage",),
        labels_none=("tina-blocked", "bot-claimed"),
    )
    assert render(cfg.track("smoke")) == (
        'repo:acme/api is:issue is:open no:assignee label:"needs-triage"'
        ' -label:"tina-blocked" -label:"bot-claimed"'
    )


@pytest.mark.parametrize(
    ("snippet", "message"),
    [
        ('project = "VUL; DROP"', "project key"),
        ('[vul.filters]\nTeam = ["Team \\" OR 1=1"]', "filter value"),
        ("[vul.filters]\nTeam = []", "at least one value"),
        ('[vul.filters]\n"Team)" = ["x"]', "filter field"),
    ],
)
def test_interpolated_jira_values_are_validated(tmp_path: Path, snippet: str, message: str) -> None:
    text = JIRA_PARTS.replace('extra = "labels not in (wontfix)"', "").replace(
        '[vul.filters]\nTeam = ["Payments", "Search"]', ""
    )
    text = text.replace('project = "VUL"', 'project = "VUL"\n' + snippet, 1)
    if 'project = "VUL; DROP"' in snippet:
        text = text.replace('project = "VUL"\n', "", 1)

    with pytest.raises(config.ConfigError, match=message):
        config.load(write(tmp_path, text))


def test_github_labels_reject_quotes(tmp_path: Path) -> None:
    text = MINIMAL.replace(
        'source = "jira"\nproject = "VUL"',
        'source = "github"\nrepo = "acme/api"\nlabels = [\'a"b\']',
    )

    with pytest.raises(config.ConfigError, match="may not contain quotes"):
        config.load(write(tmp_path, text))


def test_a_malformed_repo_is_rejected(tmp_path: Path) -> None:
    text = MINIMAL.replace(
        'source = "jira"\nproject = "VUL"', 'source = "github"\nrepo = "not-a-repo"'
    )

    with pytest.raises(config.ConfigError, match='must be "owner/name"'):
        config.load(write(tmp_path, text))


def test_a_jira_scope_is_refused_on_a_github_track(tmp_path: Path) -> None:
    text = MINIMAL.replace(
        'source = "jira"\nproject = "VUL"',
        'source = "github"\nrepo = "acme/api"\nproject = "X"',
    )

    with pytest.raises(config.ConfigError, match="project only applies to jira"):
        config.load(write(tmp_path, text))


@pytest.mark.parametrize(
    ("snippet", "feature"),
    [
        ('status = "Open"', "status"),
        ('extra = "milestone:v2"', "extra"),
        ('[smoke.filters]\nTeam = ["x"]', "filters"),
    ],
)
def test_a_feature_the_source_lacks_is_refused_by_name(
    tmp_path: Path, snippet: str, feature: str
) -> None:
    """The source declares what it compiles; the mismatch names the key."""
    text = MINIMAL.replace(
        '[vul]\nsource = "jira"\nproject = "VUL"',
        f'[smoke]\nsource = "github"\nrepo = "acme/api"\n{snippet}',
    )

    with pytest.raises(config.ConfigError, match=f'source = "github" does not support {feature}'):
        config.load(write(tmp_path, text))


def test_labels_are_a_feature_both_sources_compile(tmp_path: Path) -> None:
    cfg = config.load(write(tmp_path, MINIMAL + 'labels = ["x"]\n'))

    assert cfg.track("vul").query.labels_all == ("x",)
    assert 'labels = "x"' in render(cfg.track("vul"))


def test_blocked_transition_is_jira_only(tmp_path: Path) -> None:
    text = MINIMAL.replace(
        'source = "jira"\nproject = "VUL"',
        'source = "github"\nrepo = "acme/api"\nblocked_transition = "X"',
    )

    with pytest.raises(config.ConfigError, match="blocked_transition only applies to jira"):
        config.load(write(tmp_path, text))


def test_structured_inputs_are_queue_only(tmp_path: Path) -> None:
    text = MINIMAL.replace('source = "jira"\nproject = "VUL"', 'mode = "sweep"\nproject = "VUL"')

    with pytest.raises(config.ConfigError, match="remove: project"):
        config.load(write(tmp_path, text))


# --- max_concurrency ----------------------------------------------------------


def test_max_concurrency_must_be_a_positive_integer(tmp_path: Path) -> None:
    with pytest.raises(config.ConfigError, match="max_concurrency"):
        config.load(write(tmp_path, MINIMAL + "max_concurrency = 0\n"))
    with pytest.raises(config.ConfigError, match="max_concurrency"):
        config.load(write(tmp_path, MINIMAL + "max_concurrency = true\n"))

    assert (
        config.load(write(tmp_path, MINIMAL + "max_concurrency = 20\n"))
        .track("vul")
        .max_concurrency
        == 20
    )


def test_max_concurrency_is_queue_only(tmp_path: Path) -> None:
    text = MINIMAL.replace(
        'source = "jira"\nproject = "VUL"', 'mode = "sweep"\nmax_concurrency = 2'
    )

    with pytest.raises(config.ConfigError, match="remove: max_concurrency"):
        config.load(write(tmp_path, text))


# --- environment overrides for the top-level paths -------------------------


def test_tina_tracks_dir_overrides_the_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(config.TRACKS_DIR_VAR, "/app/tracks")
    cfg = config.load(write(tmp_path, MINIMAL))

    assert cfg.tracks_dir == Path("/app/tracks")
    assert cfg.track_dir(cfg.track("vul")) == Path("/app/tracks/vul")


def test_tina_artifacts_dir_overrides_the_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(config.ARTIFACTS_DIR_VAR, "/mnt/sessions")
    cfg = config.load(write(tmp_path, MINIMAL))

    assert cfg.artifacts_path() == Path("/mnt/sessions")


# --- track lookup, cloud run job template, harness retry ---------------------


def test_track_lookup_is_case_insensitive(tmp_path: Path) -> None:
    cfg = config.load(write(tmp_path, MINIMAL))

    assert cfg.track("VUL").name == "vul"
    with pytest.raises(config.ConfigError, match="no track named"):
        cfg.track("bug")


def test_cloudrun_job_may_carry_the_track_placeholder(tmp_path: Path) -> None:
    text = (
        MINIMAL.replace('harness = "pi"', 'harness = "pi"\nexecutor = "cloudrun"')
        + '\n[executors.cloudrun]\nproject = "p"\nregion = "r"\njob = "factory-{track}"\n'
    )
    cfg = config.load(write(tmp_path, text))

    assert cfg.cloudrun_options().job_name("vul") == "factory-vul"
    assert cfg.cloudrun_options().job_path("vul") == "projects/p/locations/r/jobs/factory-vul"


def test_harness_retry_rules_are_parsed_in_order(tmp_path: Path) -> None:
    text = MINIMAL.replace(
        'command = ["pi", "--prompt-file", "{prompt_file}"]',
        'command = ["pi", "--prompt-file", "{prompt_file}"]\n'
        "retry = [\n"
        '  { markers = ["Quota exceeded"], waits = [60, 300], reason = "quota" },\n'
        '  { markers = ["overloaded_error", "RESOURCE_EXHAUSTED"], waits = [30] },\n'
        "]",
    )
    cfg = config.load(write(tmp_path, text))
    rules = cfg.harness_config().retry

    assert [r.reason for r in rules] == ["quota", ""]
    assert rules[0].waits == [60.0, 300.0]
    assert rules[1].matches("HTTP 429 RESOURCE_EXHAUSTED")
    assert not rules[1].matches("all good")


def test_a_retry_rule_needs_markers_and_waits(tmp_path: Path) -> None:
    text = MINIMAL.replace(
        'command = ["pi", "--prompt-file", "{prompt_file}"]',
        'command = ["pi", "--prompt-file", "{prompt_file}"]\n'
        "retry = [{ markers = [], waits = [1] }]",
    )

    with pytest.raises(config.ConfigError, match="markers"):
        config.load(write(tmp_path, text))


# --- overrides for library callers -----------------------------------------------


def test_load_accepts_path_overrides_for_embedders(tmp_path: Path) -> None:
    """A program embedding tina keeps its own environment contract and passes
    the paths in; nothing here reads TINA_*."""
    cfg = config.load(
        write(tmp_path, MINIMAL),
        tracks_dir="/app/skills",
        control="/mnt/policy/control.toml",
        artifacts_dir="/mnt/sessions",
    )

    assert cfg.track_dir(cfg.track("vul")) == Path("/app/skills/vul")
    assert cfg.control_path() == Path("/mnt/policy/control.toml")
    assert cfg.artifacts_path() == Path("/mnt/sessions")


def test_an_override_wins_over_the_environment_and_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(config.TRACKS_DIR_VAR, "/from/env")
    text = MINIMAL.replace('harness = "pi"', 'harness = "pi"\ntracks_dir = "from-file"', 1)

    cfg = config.load(write(tmp_path, text), tracks_dir="/from/caller")

    assert cfg.tracks_dir == Path("/from/caller")


# --- the models list ----------------------------------------------------------------

MODELLED = """
harness = "pi"
models = ["fast", "frontier"]

[harnesses.pi]
command = ["pi", "-p", "@{prompt_file}", "--model", "{model}"]

[vul]
source = "jira"
project = "VUL"
model = "frontier"
"""


def test_a_tracks_model_must_be_listed_when_models_is_set(tmp_path: Path) -> None:
    cfg = config.load(write(tmp_path, MODELLED))
    assert cfg.models == ["fast", "frontier"]
    assert cfg.allows_model("fast") and not cfg.allows_model("other")

    with pytest.raises(config.ConfigError, match="'other' is not in `models`"):
        config.load(write(tmp_path, MODELLED.replace('model = "frontier"', 'model = "other"')))


def test_no_models_list_means_unconstrained(tmp_path: Path) -> None:
    cfg = config.load(write(tmp_path, MODELLED.replace('models = ["fast", "frontier"]\n', "")))
    assert cfg.models == []
    assert cfg.allows_model("anything")


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ('models = ["fast", "fast"]', "listed twice"),
        ('models = ["a b"]', "no whitespace"),
        ('models = [""]', "non-empty"),
    ],
)
def test_models_entries_are_validated(tmp_path: Path, value: str, message: str) -> None:
    with pytest.raises(config.ConfigError, match=message):
        config.load(write(tmp_path, MODELLED.replace('models = ["fast", "frontier"]', value)))
