from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tina import doctor
from tina.models import WorkItem
from tina.sources.base import ClaimPrognosis, SourceError

CONFIG = """
harness = "fake"

[harnesses.fake]
command = ["{binary}", "{{prompt_file}}"]

[vul]
source = "jira"
query = "project = VUL"

[audit]
mode = "sweep"
"""

# A harness that answers: the probe prompt names the outcome path, and this
# script writes the file it asks for. `{outcome_dir}` is the run's workdir.
ANSWERING = """
harness = "fake"
models = ["fast", "slow"]

[harnesses.fake]
command = ["{python}", "{script}", "{{prompt_file}}", "{{outcome_dir}}", "{{model}}"]
retry = [{{ markers = ["RESOURCE_EXHAUSTED"], waits = [1], reason = "capacity" }}]

[vul]
source = "jira"
query = "project = VUL"
model = "fast"
"""

ANSWERS = """\
import pathlib, sys
model = sys.argv[3]
if model == "slow":
    print("provider: RESOURCE_EXHAUSTED, retry later")
    raise SystemExit(1)
if model == "dead":
    print("no such model")
    raise SystemExit(2)
report = '{"outcome": "no_action_needed", "details": "probe"}'
pathlib.Path(sys.argv[2], "outcome.json").write_text(report)
"""


class FakeSource:
    def __init__(self, identity: str = "bot", matched: int = 2, refuse: bool = False) -> None:
        self.identity = identity
        self.matched = matched
        self.refuse = refuse

    def login(self) -> str:
        if self.refuse:
            raise SourceError("jira: GET /rest/api/3/myself returned 401", fix="Check the token.")
        return self.identity

    def query(self, q: str) -> list[WorkItem]:
        return [WorkItem(id=f"VUL-{i}", source="jira") for i in range(self.matched)]

    # The rest of the Source protocol, never reached by doctor's read-only probes.
    def get(self, item_id: str) -> WorkItem:
        raise AssertionError("doctor never fetches an item")

    def matches(self, item_id: str, q: str) -> bool:
        raise AssertionError("doctor never re-checks an item")

    def claim(self, item: WorkItem) -> bool:
        raise AssertionError("doctor never claims")

    def claim_prognosis(self, item: WorkItem) -> ClaimPrognosis:
        raise AssertionError("doctor never asks a prognosis")

    def claimed(self, q: str) -> list[WorkItem]:
        raise AssertionError("doctor never counts claims")

    def annotate(self, item: WorkItem, comment: str) -> None:
        raise AssertionError("doctor never writes")

    def block(self, item: WorkItem) -> None:
        raise AssertionError("doctor never writes")


def project(tmp_path: Path, binary: str = "python3", skills: bool = True) -> Path:
    path = tmp_path / "tina.toml"
    path.write_text(CONFIG.format(binary=binary))
    if skills:
        for name in ("vul", "audit"):
            (tmp_path / "tracks" / name).mkdir(parents=True)
            (tmp_path / "tracks" / name / "SKILL.md").write_text("---\nname: x\n---\n")
    return path


def by_name(checks: list[doctor.Check]) -> dict[str, doctor.Check]:
    return {check.name: check for check in checks}


def diagnose(*args, **kwargs) -> list[doctor.Check]:
    """`doctor.diagnose` with the model probes off unless a test turns them on:
    the plain fixture's harness is `python3 <prompt_file>`, which would try to
    run the prompt as a program."""
    kwargs.setdefault("probe_models", False)
    return doctor.diagnose(*args, **kwargs)


def test_a_healthy_deployment_passes_every_probe(tmp_path: Path) -> None:
    checks = by_name(diagnose(project(tmp_path), build_source=lambda track, **kw: FakeSource()))

    assert all(check.ok for check in checks.values()), checks
    assert checks["harness 'fake' on PATH"].detail.endswith("python3")
    assert checks["executor 'local'"].detail == "LocalExecutor"
    assert checks["control policy"].detail == "none configured; defaults apply"
    assert checks["[vul] jira credentials"].detail == "acting as bot"
    assert checks["[vul] query"].detail == "2 item(s) match now"
    assert "[audit] skill" in checks
    assert "[audit] query" not in checks, "a sweep has no source to probe"


def test_a_missing_harness_binary_fails_that_probe_only(tmp_path: Path) -> None:
    checks = by_name(
        diagnose(
            project(tmp_path, binary="no-such-harness"),
            build_source=lambda track, **kw: FakeSource(),
        )
    )

    assert not checks["harness 'fake' on PATH"].ok
    assert "must install it" in checks["harness 'fake' on PATH"].detail
    assert checks["[vul] query"].ok


def test_refused_credentials_stop_before_the_query(tmp_path: Path) -> None:
    checks = by_name(
        diagnose(project(tmp_path), build_source=lambda track, **kw: FakeSource(refuse=True))
    )

    assert not checks["[vul] jira credentials"].ok
    assert "401" in checks["[vul] jira credentials"].detail
    assert "Check the token." in checks["[vul] jira credentials"].detail
    assert "[vul] query" not in checks


def test_a_missing_skill_is_reported(tmp_path: Path) -> None:
    checks = by_name(
        diagnose(project(tmp_path, skills=False), build_source=lambda track, **kw: FakeSource())
    )

    assert not checks["[vul] skill"].ok
    assert not checks["[audit] skill"].ok


def test_an_invalid_control_file_reads_as_failing_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TINA_CONTROL_INLINE", "paused = maybe")

    checks = by_name(diagnose(project(tmp_path), build_source=lambda track, **kw: FakeSource()))

    assert not checks["control policy"].ok
    assert "failing closed" in checks["control policy"].detail


def test_a_bad_config_is_the_only_check(tmp_path: Path) -> None:
    path = tmp_path / "tina.toml"
    path.write_text("nonsense = [")

    checks = diagnose(path)

    assert len(checks) == 1
    assert not checks[0].ok
    assert "invalid TOML" in checks[0].detail


def test_only_narrows_to_one_track(tmp_path: Path) -> None:
    checks = by_name(
        diagnose(project(tmp_path), only="audit", build_source=lambda track, **kw: FakeSource())
    )

    assert "[audit] skill" in checks
    assert "[vul] skill" not in checks


def test_path_overrides_reach_the_probes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = project(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    (tmp_path / "tracks").rename(elsewhere)
    (tmp_path / "control.toml").write_text("paused = true\nmax_concurrency = 2\n")

    checks = by_name(
        diagnose(
            path,
            build_source=lambda track, **kw: FakeSource(),
            tracks_dir=elsewhere,
            control=tmp_path / "control.toml",
        )
    )

    assert checks["[vul] skill"].ok and checks["[vul] skill"].detail.startswith(str(elsewhere))
    assert checks["control policy"].detail.endswith("paused true, max_concurrency 2")


# --- the model probes ------------------------------------------------------------


def answering(tmp_path: Path) -> Path:
    script = tmp_path / "answers.py"
    script.write_text(ANSWERS)
    path = tmp_path / "tina.toml"
    path.write_text(ANSWERING.format(python=sys.executable, script=script))
    (tmp_path / "tracks" / "vul").mkdir(parents=True)
    (tmp_path / "tracks" / "vul" / "SKILL.md").write_text("---\nname: vul\n---\n")
    return path


def test_every_listed_model_is_probed_through_the_harness(tmp_path: Path) -> None:
    checks = by_name(
        doctor.diagnose(answering(tmp_path), build_source=lambda track, **kw: FakeSource())
    )

    assert checks["model 'fast' answers"].ok
    assert checks["model 'fast' answers"].detail == ""
    assert checks["model 'slow' answers"].ok, "a retry marker means the request reached the model"
    assert checks["model 'slow' answers"].detail == "answered, throttled: capacity"


def test_a_model_that_does_not_answer_fails_its_probe(tmp_path: Path) -> None:
    path = answering(tmp_path)
    path.write_text(
        path.read_text().replace('models = ["fast", "slow"]', 'models = ["fast", "dead"]')
    )

    checks = by_name(doctor.diagnose(path, build_source=lambda track, **kw: FakeSource()))

    assert not checks["model 'dead' answers"].ok
    assert checks["model 'dead' answers"].detail == "exit 2: no such model"
    assert checks["model 'fast' answers"].ok


def test_without_a_models_list_the_tracks_models_are_probed(tmp_path: Path) -> None:
    path = answering(tmp_path)
    path.write_text(path.read_text().replace('models = ["fast", "slow"]\n', ""))

    checks = by_name(doctor.diagnose(path, build_source=lambda track, **kw: FakeSource()))

    assert "model 'fast' answers" in checks
    assert "model 'slow' answers" not in checks


def test_probes_are_skipped_on_request_and_when_the_harness_is_missing(tmp_path: Path) -> None:
    path = answering(tmp_path)

    skipped = by_name(
        doctor.diagnose(path, build_source=lambda track, **kw: FakeSource(), probe_models=False)
    )
    assert not any(name.startswith("model ") for name in skipped)

    path.write_text(path.read_text().replace(sys.executable, "no-such-harness"))
    missing = by_name(doctor.diagnose(path, build_source=lambda track, **kw: FakeSource()))
    assert not missing["harness 'fake' on PATH"].ok
    assert not any(name.startswith("model ") for name in missing), "nothing to probe with"
