from __future__ import annotations

from pathlib import Path

from tina import validate

CONFIG = """
harness = "pi"

[harnesses.pi]
command = ["pi", "--prompt-file", "{prompt_file}"]

[remediate]
source = "jira"
project = "VUL"

[audit]
mode = "sweep"
"""

ROUTER = """\
---
name: remediate
description: Fix the vulnerability named by the work item.
---

# Router

Work item: $WORK_ITEM_KEY

- Code we own: follow [paths/code.md](paths/code.md).
- Anything else: stop.
"""

SWEEP = """\
---
name: audit
description: Sweep the repo for cruft and file issues.
---

Look around. Nothing here refers to a work item.
"""


def project(tmp_path: Path, router: str = ROUTER, paths: dict[str, str] | None = None) -> Path:
    (tmp_path / "tina.toml").write_text(CONFIG)
    remediate = tmp_path / "tracks" / "remediate"
    (remediate / "paths").mkdir(parents=True)
    (remediate / "SKILL.md").write_text(router)
    for name, text in (paths if paths is not None else {"code.md": "1. Fix it.\n"}).items():
        (remediate / "paths" / name).write_text(text)
    audit = tmp_path / "tracks" / "audit"
    audit.mkdir()
    (audit / "SKILL.md").write_text(SWEEP)
    return tmp_path / "tina.toml"


def test_a_conforming_project_passes_with_a_summary(tmp_path: Path) -> None:
    report = validate.validate(project(tmp_path))

    assert report.ok, report.errors
    assert report.summary[0] == "audit: (sweep)"
    assert report.summary[1].startswith("remediate: project = VUL AND status"), (
        "the summary shows the native query the source would run"
    )
    assert report.summary[-1].endswith("2 track(s) conform")
    assert report.warnings == []


def test_a_config_error_is_the_whole_report(tmp_path: Path) -> None:
    path = tmp_path / "tina.toml"
    path.write_text('harness = "pi"\n')

    report = validate.validate(path)

    assert len(report.errors) == 1
    assert "harness 'pi' has no [harnesses.pi]" in report.errors[0]
    assert report.summary == []


def test_a_missing_skill_directory_is_an_error(tmp_path: Path) -> None:
    path = project(tmp_path)
    (tmp_path / "tracks" / "audit" / "SKILL.md").unlink()

    report = validate.validate(path)

    assert any("[audit]: skill not found" in e for e in report.errors)


def test_frontmatter_must_name_the_directory(tmp_path: Path) -> None:
    report = validate.validate(project(tmp_path, ROUTER.replace("name: remediate", "name: fix")))

    assert any("does not match the skill directory" in e for e in report.errors)


def test_frontmatter_is_required(tmp_path: Path) -> None:
    report = validate.validate(project(tmp_path, "# No frontmatter\n"))

    assert any("missing or unterminated YAML frontmatter" in e for e in report.errors)


def test_the_router_must_dispatch_to_files_that_exist(tmp_path: Path) -> None:
    report = validate.validate(project(tmp_path, paths={}))

    errors = "\n".join(report.errors)
    assert "dispatches to 'paths/code.md' which does not exist" in errors
    assert "reference 'paths/code.md' does not resolve" in errors


def test_an_orphan_path_is_dead_content(tmp_path: Path) -> None:
    report = validate.validate(
        project(tmp_path, paths={"code.md": "1.\n", "infra.md": "never dispatched\n"})
    )

    assert any("paths/infra.md: not referenced by the router" in e for e in report.errors)


def test_absolute_and_variable_indirected_references_are_rejected(tmp_path: Path) -> None:
    router = ROUTER + "\nSee /app/tracks/remediate/references/x.md and $SKILL_DIR/paths/y.md.\n"
    report = validate.validate(project(tmp_path, router))

    errors = "\n".join(report.errors)
    assert "absolute path reference '/app/tracks/remediate/references/x.md'" in errors
    assert "variable-indirected reference '$SKILL_DIR/paths/y.md'" in errors


def test_unknown_tokens_in_prose_are_rejected_but_code_is_exempt(tmp_path: Path) -> None:
    router = ROUTER + "\nThen read $TICKET.\n\n```sh\necho $HOME\n```\n\nRun `echo $PATH`.\n"
    report = validate.validate(project(tmp_path, router))

    assert len(report.errors) == 1
    assert "'$TICKET' is not substituted by tina" in report.errors[0]


def test_the_work_item_token_is_an_error_on_a_sweep(tmp_path: Path) -> None:
    path = project(tmp_path)
    (tmp_path / "tracks" / "audit" / "SKILL.md").write_text(SWEEP + "\nItem: $WORK_ITEM_KEY\n")

    report = validate.validate(path)

    assert any("sweep track has no work item" in e for e in report.errors)


def test_shared_reference_drift_is_an_error(tmp_path: Path) -> None:
    path = project(tmp_path)
    canonical = tmp_path / "tracks" / "remediate" / "references"
    canonical.mkdir()
    (canonical / "shared.md").write_text("The truth.\n")
    copy = tmp_path / "tracks" / "audit" / "references"
    copy.mkdir()
    (copy / "shared.md").write_text(
        "<!-- shared: remediate/references/shared.md -->\n\nA stale copy.\n"
    )

    report = validate.validate(path)

    assert any("diverges from its canonical copy" in e for e in report.errors)

    (copy / "shared.md").write_text(
        "<!-- shared: remediate/references/shared.md -->\n\nThe truth.\n"
    )
    assert validate.validate(path).ok


def test_an_orphan_skill_directory_is_a_warning_not_an_error(tmp_path: Path) -> None:
    path = project(tmp_path)
    stray = tmp_path / "tracks" / "stray"
    stray.mkdir()
    (stray / "SKILL.md").write_text("---\nname: stray\ndescription: x\n---\n")

    report = validate.validate(path)

    assert report.ok
    assert len(report.warnings) == 1
    assert "stray has no track table" in report.warnings[0]


def test_only_scopes_the_skill_checks_and_skips_the_orphan_warning(tmp_path: Path) -> None:
    path = project(tmp_path, paths={})
    stray = tmp_path / "tracks" / "stray"
    stray.mkdir()
    (stray / "SKILL.md").write_text("---\nname: stray\ndescription: x\n---\n")

    report = validate.validate(path, only="audit")

    assert report.ok, "remediate's broken path is out of scope"
    assert report.summary[0] == "audit: (sweep)"
    assert report.warnings == []


def test_only_with_an_unknown_track_is_an_error(tmp_path: Path) -> None:
    report = validate.validate(project(tmp_path), only="nope")

    assert not report.ok
    assert "no track named 'nope'" in report.errors[0]


def test_a_disabled_track_is_still_checked(tmp_path: Path) -> None:
    path = project(tmp_path, paths={})
    path.write_text(CONFIG.replace('project = "VUL"', 'project = "VUL"\nenabled = false'))

    report = validate.validate(path)

    assert not report.ok
    assert any("paths/code.md" in e for e in report.errors)


def test_a_tracks_dir_override_is_where_the_skills_are_read_from(tmp_path: Path) -> None:
    """An embedder's skills may live far from its registry."""
    path = project(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    (tmp_path / "tracks").rename(elsewhere)

    assert not validate.validate(path).ok, "the config's own tracks_dir no longer resolves"
    report = validate.validate(path, tracks_dir=elsewhere)
    assert report.ok, report.errors
    assert report.summary[-1].startswith(str(elsewhere))
