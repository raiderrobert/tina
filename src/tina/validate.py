"""Admission: static checks on the config and every track skill, before any run.

Nothing else checks a track until an agent is holding a claimed item, which
is the most expensive possible place to find a typo'd filename. These checks
are cheap and catch most of it:

1. The config loads — every table validates, every adapter name resolves.
   Delegated to `tina.config.load`, the same code the runner uses.
2. Skill frontmatter conforms to the Agent Skills shape: it parses, `name`
   matches the directory, `name` is a lowercase slug, `description` is bounded.
3. Router-to-paths closure for multi-file skills: every `paths/*.md` the
   router dispatches to exists, and every file in `paths/` is dispatched to
   — an orphan is dead content that still ships.
4. Every relative `paths/…` and `references/…` reference resolves; no
   absolute paths and no variable-indirected paths to skill content. Both
   work on the author's machine and break in the image.
5. Every bare `$TOKEN` in skill prose is one Tina substitutes. Code fences and
   inline code are exempt — they hold shell the agent runs.
6. A reference file carrying a `<!-- shared: <track>/references/<file>.md -->`
   marker matches its canonical copy byte for byte. Skills have no cross-skill
   references, so shared knowledge is duplicated; the marker turns the drift
   that invites into a failure.
7. Config and `tracks_dir` agree in both directions: a track whose skill
   directory is missing is an error; a skill directory no track names is a
   warning, so a skill can land ahead of its registration.

Disabled tracks are checked like any other — a track that ships must not rot
while it is off.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from tina.config import Config, ConfigError, TrackConfig
from tina.config import load as load_config
from tina.prompt import RUNNER_TOKENS, SKILL_FILE, WORK_ITEM_TOKEN


@dataclass(frozen=True)
class FieldConstraint:
    max_len: int
    pattern: re.Pattern[str] | None = None

    def satisfied_by(self, value: str) -> bool:
        if len(value) > self.max_len:
            return False
        return self.pattern is None or self.pattern.match(value) is not None


NAME = FieldConstraint(max_len=64, pattern=re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$"))
DESCRIPTION = FieldConstraint(max_len=1024)

# Prose references to skill content. `scripts/` is deliberately not matched:
# a script is invoked from a code block, where an example path is common and
# a real one fails loudly at run time anyway.
_REL_REF = re.compile(r"(?:paths|references)/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*\.md")
_TOKEN = re.compile(r"\$\{?([A-Z][A-Z0-9_]*)\}?")
_FENCE = re.compile(r"^[ \t]*```.*?^[ \t]*```", re.MULTILINE | re.DOTALL)
_CODE_SPAN = re.compile(r"`[^`\n]*`")


@dataclass(frozen=True)
class RefRule:
    """A forbidden reference form: what to match and what to say about it."""

    pattern: re.Pattern[str]
    message: str


_REF_RULES = (
    # Absolute filesystem references to baked content. /tmp scratch paths are
    # runtime working files, not references to skill content, and pass.
    RefRule(
        pattern=re.compile(r"/(?:etc|app|usr|opt|srv|home|root|mnt|var)/[A-Za-z0-9$_./-]+"),
        message="absolute path reference",
    ),
    RefRule(
        pattern=re.compile(r"\$\{?[A-Z_]+\}?/[A-Za-z0-9$_./-]*\.md"),
        message="variable-indirected reference",
    ),
)

# `<!-- shared: vul/references/squad-slugs.md -->` — first line of a duplicated
# reference file, naming its canonical copy relative to tracks_dir.
_SHARED_MARKER = re.compile(
    r"\A<!--\s*shared:\s*([A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*\.md)\s*-->[ \t]*\n\n?"
)


@dataclass
class Report:
    """Everything a validation run found, in the three groups a caller reports.

    `errors` fail the run; `summary` is one line per track in scope, populated
    only when there are no errors (an unloadable config has nothing to
    summarise); `warnings` never fail the run.
    """

    errors: list[str] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate(config_path: Path | str, only: str | None = None) -> Report:
    """Run every check. `only` scopes the skill-level checks to one track.

    Config-level errors are always global — the file must parse whole — and
    the orphan-directory warning describes the whole `tracks_dir`, so a scoped
    run skips it rather than reporting a neighbour's problem under one track.
    """
    report = Report()
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        report.errors.append(str(exc))
        return report

    tracks = dict(config.tracks)
    if only is not None:
        try:
            track = config.track(only)
        except ConfigError as exc:
            report.errors.append(str(exc))
            return report
        tracks = {track.name: track}

    tracks_root = _tracks_root(config)
    for track in tracks.values():
        report.errors.extend(check_track(config, track, tracks_root))

    if report.ok:
        for name in sorted(tracks):
            track = tracks[name]
            what = "(sweep)" if track.mode == "sweep" else track.query
            flag = "" if track.enabled else " [disabled]"
            report.summary.append(f"{name}{flag}: {what}")
        report.summary.append(f"{tracks_root}: {len(tracks)} track(s) conform")

    if only is None:
        report.warnings.extend(
            f"{orphan} has no track table in {config.path} — it ships but nothing can run it"
            for orphan in orphan_skill_dirs(config)
        )
    return report


def check_track(config: Config, track: TrackConfig, tracks_root: Path) -> list[str]:
    """Every skill-level check for one track. Empty means conforming."""
    skill_dir = config.track_dir(track)
    skill_md = skill_dir / SKILL_FILE
    if not skill_md.is_file():
        return [f"{config.path}: [{track.name}]: skill not found at {skill_md}"]
    errors = []
    errors.extend(check_frontmatter(skill_dir))
    errors.extend(check_router_paths_closure(skill_dir))
    errors.extend(check_references(skill_dir))
    errors.extend(check_tokens(skill_dir, sweep=track.mode == "sweep"))
    errors.extend(shared_reference_drift(tracks_root, skill_dir))
    return errors


def parse_frontmatter(text: str) -> dict[str, str] | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        return None
    fields: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if not line.strip() or ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    return fields


def check_frontmatter(skill_dir: Path) -> list[str]:
    skill_md = skill_dir / SKILL_FILE
    fm = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    if fm is None:
        return [f"{skill_md}: missing or unterminated YAML frontmatter (Agent Skills spec)"]

    errors = []
    name = fm.get("name", "")
    description = fm.get("description", "")
    if not name:
        errors.append(f"{skill_md}: frontmatter 'name' is required (Agent Skills spec)")
    else:
        if name != skill_dir.name:
            errors.append(
                f"{skill_md}: frontmatter name {name!r} does not match the skill "
                f"directory {skill_dir.name!r} (Agent Skills spec)"
            )
        if not NAME.satisfied_by(name):
            errors.append(
                f"{skill_md}: frontmatter name {name!r} must be lowercase alphanumerics "
                f"and hyphens, at most {NAME.max_len} chars (Agent Skills spec)"
            )
    if not description:
        errors.append(f"{skill_md}: frontmatter 'description' is required (Agent Skills spec)")
    elif not DESCRIPTION.satisfied_by(description):
        errors.append(
            f"{skill_md}: frontmatter description exceeds {DESCRIPTION.max_len} chars "
            f"(Agent Skills spec)"
        )
    return errors


def check_references(skill_dir: Path) -> list[str]:
    errors = []
    for md in sorted(skill_dir.rglob("*.md")):
        text = md.read_text(encoding="utf-8")
        for rule in _REF_RULES:
            for match in rule.pattern.finditer(text):
                errors.append(
                    f"{md}: {rule.message} '{match.group(0)}' — skill references "
                    f"must be relative to the skill root"
                )
        for match in _REL_REF.finditer(text):
            if not (skill_dir / match.group(0)).is_file():
                errors.append(f"{md}: reference '{match.group(0)}' does not resolve")
    return errors


def check_tokens(skill_dir: Path, *, sweep: bool = False) -> list[str]:
    """Errors for `$TOKEN`s in prose that Tina does not substitute.

    Code fences and inline code spans are exempt: those hold shell snippets
    and output templates whose variables the agent binds at run time. Prose
    is where a token reads as runner-provided, and an unrecognised one there
    reaches the model verbatim instead of failing. A sweep has no work item,
    so the work-item token is unrecognised there too.
    """
    known = set() if sweep else RUNNER_TOKENS
    errors = []
    for md in sorted(skill_dir.rglob("*.md")):
        prose = _CODE_SPAN.sub("", _FENCE.sub("", md.read_text(encoding="utf-8")))
        for name in dict.fromkeys(m.group(1) for m in _TOKEN.finditer(prose)):
            if name in known:
                continue
            if sweep and name in RUNNER_TOKENS:
                errors.append(
                    f"{md}: '${name}' is a work-item token, and a sweep track has no work item"
                )
                continue
            errors.append(
                f"{md}: '${name}' is not substituted by tina — the only runner token is "
                f"{WORK_ITEM_TOKEN}; a placeholder the skill fills itself belongs in a "
                f"code span or fence"
            )
    return errors


def shared_reference_drift(tracks_root: Path, scope: Path) -> list[str]:
    """Errors for `shared:`-marked duplicates that diverge from their canonical copy.

    Everything after the marker line must equal the canonical file exactly.
    `scope` narrows which marked files are checked to one skill directory;
    canonical paths still resolve against `tracks_root`.
    """
    if not scope.is_dir():
        return []
    errors: list[str] = []
    for md in sorted(scope.rglob("*.md")):
        marker = _SHARED_MARKER.match(md.read_text(encoding="utf-8"))
        if marker is None:
            continue
        canonical = tracks_root / marker.group(1)
        if canonical.resolve() == md.resolve():
            errors.append(f"{md}: 'shared:' marker points at itself — name the canonical copy")
            continue
        if not canonical.is_file():
            errors.append(
                f"{md}: 'shared:' marker names {marker.group(1)!r}, which does not "
                f"exist under {tracks_root}"
            )
            continue
        body = md.read_text(encoding="utf-8")[marker.end() :]
        if body != canonical.read_text(encoding="utf-8"):
            errors.append(
                f"{md}: diverges from its canonical copy {canonical} — shared references "
                f"are duplicated verbatim; edit {canonical} and copy it below the marker"
            )
    return errors


def check_router_paths_closure(skill_dir: Path) -> list[str]:
    router_text = (skill_dir / SKILL_FILE).read_text(encoding="utf-8")
    dispatched = {ref for ref in _REL_REF.findall(router_text) if ref.startswith("paths/")}
    paths_dir = skill_dir / "paths"
    existing = {f"paths/{p.name}" for p in paths_dir.glob("*.md")} if paths_dir.is_dir() else set()

    errors = []
    for ref in sorted(dispatched - existing):
        errors.append(
            f"{skill_dir / SKILL_FILE}: router dispatches to '{ref}' which does not exist"
        )
    for orphan in sorted(existing - dispatched):
        errors.append(
            f"{skill_dir / orphan}: not referenced by the router — orphan paths are dead content"
        )
    return errors


def orphan_skill_dirs(config: Config) -> list[Path]:
    """Skill directories no track table names — dead content in the image."""
    root = _tracks_root(config)
    if not root.is_dir():
        return []
    registered = {config.track_dir(t).resolve() for t in config.tracks.values()}
    return sorted(
        d
        for d in root.iterdir()
        if d.is_dir() and (d / SKILL_FILE).is_file() and d.resolve() not in registered
    )


def _tracks_root(config: Config) -> Path:
    base = config.tracks_dir
    if not base.is_absolute():
        base = config.path.parent / base
    return base
