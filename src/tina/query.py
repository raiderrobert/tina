"""Structured query inputs: build a tracker query from validated parts.

Most tracks are one query shape where a list changes — a project plus the set
of opted-in teams, a repo plus a label set. Onboarding a team should be adding
one string to an array, in a PR a non-author can review, without anyone
editing a JQL expression. The builders here own the invariants every track
would otherwise re-type into every query string: open, unassigned, not
blocked, not claimed, stable sort order. `query` on the track stays the full
override for anything that does not fit.

Every value interpolated here has been validated by the config model
(charset, shape) before it arrives, so the builders concatenate without
escaping. They are pure string functions, imported by `tina.config` — nothing
here touches a tracker.
"""

from __future__ import annotations

#: The status a Jira track reads from when it sets none. A workflow label the
#: tracker owns, not Tina, so it is a config knob rather than a constant.
DEFAULT_JIRA_STATUS = "Open"


def jira_query(
    project: str,
    status: str | None,
    filters: dict[str, list[str]],
    extra: str | None,
    *,
    claim_policy: str,
    claim_label: str | None,
    claim_transition: str | None,
    blocked_label: str | None,
) -> str:
    """JQL for one project's open, unassigned, unblocked items, oldest first.

    `filters` becomes one `"Field" in ("a", "b")` clause per field — the
    field name is quoted, so custom fields with spaces work. Under `claim =
    "assign"` with a transition, items the bot holds that are still in the
    queued status are offered back: the transition, not the assignee, is what
    excludes an item, so a bot-held item still queued was reopened upstream
    and belongs in the queue again. The blocked and claim labels are excluded
    with the `labels IS EMPTY OR` guard, because JQL's `not in` does not match
    issues that have no labels at all.
    """
    clauses = [f"project = {project}", f'status = "{status or DEFAULT_JIRA_STATUS}"']
    for field, values in filters.items():
        quoted = ", ".join(f'"{value}"' for value in values)
        clauses.append(f'"{field}" in ({quoted})')
    if claim_policy == "assign" and claim_transition:
        clauses.append("(assignee IS EMPTY OR assignee = currentUser())")
    else:
        clauses.append("assignee IS EMPTY")
    excluded = [label for label in (blocked_label, claim_label) if label]
    if excluded:
        quoted = ", ".join(f'"{label}"' for label in excluded)
        clauses.append(f"(labels IS EMPTY OR labels not in ({quoted}))")
    if extra:
        clauses.append(f"({extra})")
    return " AND ".join(clauses) + " ORDER BY created ASC"


def github_query(
    repo: str,
    labels: list[str],
    *,
    claim_label: str | None,
    blocked_label: str | None,
) -> str:
    """Issue search for one repo's open, unassigned, unblocked issues.

    Every required label is its own `label:` qualifier (GitHub ANDs them); the
    blocked label and, under `claim = "label"`, the claim label are excluded.
    Values are quoted so labels with spaces survive tokenization — the same
    quoted form `claimed_label_search` and the eligibility re-check read.
    """
    parts = [f"repo:{repo}", "is:issue", "is:open", "no:assignee"]
    parts += [f'label:"{label}"' for label in labels]
    parts += [f'-label:"{label}"' for label in (blocked_label, claim_label) if label]
    return " ".join(parts)
