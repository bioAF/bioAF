"""change_7.5 section 2.6: revisions of the analysis selection, and what each change invalidates.

Carried forward from 7.4 section 2.2. The selection (`plan.analysis_selection_json`) carries a
`revision` that increments whenever any of its parts changes: the claim, the contrast, the input, the
sample mapping, the predicate, or the route. A superseded revision is kept as history, never
overwritten in place.

Every artifact computed from a selection records the revision it was computed for
(`evidence["artifact_revisions"]`). A change invalidates exactly what depends on it, and a stale
artifact is never reused and never rendered as current: it moves to `evidence["selection_history"]`,
which the report shows as "computed for an earlier selection".
"""

from __future__ import annotations

from datetime import datetime, timezone

# The evidence keys computed from a selection.
REVISION_SCOPED = (
    "deposit_inspection",
    "deposit_metadata_association",
    "level3",
    "level3_result",
    "classification_result",
    "author_consistency",
)
FINDING_CLAIM = "finding_claim"
ANALYSIS_RUN = "analysis_run_id"

# Which selection field each part is, and what a change to it invalidates.
_FIELDS = {
    "claim": "claim_index",
    "contrast": "contrast_index",
    "check": "check",
    "input": "input",
    "sample_mapping": "sample_mapping",
    "predicate": "predicate",
    "workflow": "workflow",
    "experiment": "reported_experiment_id",
}
_EVERYTHING = frozenset(REVISION_SCOPED) | {FINDING_CLAIM, ANALYSIS_RUN}
_DEPENDENTS = {
    "claim": _EVERYTHING,
    "contrast": _EVERYTHING,
    "check": _EVERYTHING,
    "experiment": _EVERYTHING,
    "workflow": _EVERYTHING,
    # The input decides what was inspected and associated; the author results only when the input was
    # the result table itself, which stage 3 records on the input.
    "input": frozenset(REVISION_SCOPED) | {ANALYSIS_RUN},
    "sample_mapping": frozenset({"deposit_metadata_association", "level3", "level3_result", "classification_result", ANALYSIS_RUN}),
    "predicate": frozenset(
        {"level3", "level3_result", "classification_result", "author_consistency", FINDING_CLAIM, ANALYSIS_RUN}
    ),
}
_BY_FIELD = {field: part for part, field in _FIELDS.items()}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def current_revision(plan) -> int | None:
    current = (getattr(plan, "analysis_selection_json", None) or {}).get("current") or {}
    revision = current.get("revision")
    return revision if isinstance(revision, int) else None


def revise(
    record: dict | None,
    changes: dict,
    *,
    decided_by: str,
    reason: str,
    confidence: float | None = None,
    model: str | None = None,
) -> tuple[dict, set[str]]:
    """A new revision with ``changes`` (selection fields), and the parts that changed. An unchanged
    selection is returned as it is, with no new revision."""
    record = dict(record or {})
    current = dict(record.get("current") or {})
    changed = {_BY_FIELD.get(field, field) for field, value in changes.items() if current.get(field) != value}
    if not changed and current:
        return record, set()
    history = list(record.get("history") or [])
    if current:
        history.append({**current, "superseded": True})
    record["current"] = {
        **current,
        **changes,
        "revision": (current.get("revision") or 0) + 1,
        "decided_by": decided_by,
        "reason": reason,
        "confidence": confidence,
        "model": model,
        "superseded": False,
        "at": _now(),
    }
    record["history"] = history
    return record, changed


def _move(study, plan, keys: set[str], *, revision: int | None, why: list[str]) -> list[str]:
    evidence = dict(study.evidence_json or {})
    moved: dict = {}
    for key in REVISION_SCOPED:
        if key in keys and key in evidence:
            moved[key] = evidence.pop(key)
    if FINDING_CLAIM in keys and getattr(plan, "finding_claim_json", None):
        moved[FINDING_CLAIM] = plan.finding_claim_json
        plan.finding_claim_json = None
    if ANALYSIS_RUN in keys and getattr(study, "analysis_run_id", None):
        moved[ANALYSIS_RUN] = study.analysis_run_id
        study.analysis_run_id = None
    stamps = {k: v for k, v in (evidence.get("artifact_revisions") or {}).items() if k not in moved}
    if moved:
        evidence["selection_history"] = list(evidence.get("selection_history") or []) + [
            {"revision": revision, "artifacts": moved, "invalidated_by": why, "at": _now()}
        ]
    if stamps or "artifact_revisions" in evidence:
        evidence["artifact_revisions"] = stamps
    study.evidence_json = evidence
    return sorted(moved)


def invalidate(study, plan, changed: set[str], *, revision: int | None) -> list[str]:
    """Move whatever depends on the ``changed`` parts into history, recorded with the ``revision`` it
    was computed for. Returns the invalidated keys."""
    keys: set[str] = set()
    for part in changed:
        keys |= _DEPENDENTS.get(part, _EVERYTHING)
    moved = _move(study, plan, keys, revision=revision, why=sorted(changed))
    sync_revisions(study, plan)
    return moved


def stale_artifacts(study, plan) -> list[str]:
    """The revision-scoped artifacts computed for an earlier revision than the current one."""
    revision = current_revision(plan)
    if revision is None:
        return []
    stamps = (study.evidence_json or {}).get("artifact_revisions") or {}
    return sorted(k for k, v in stamps.items() if isinstance(v, int) and v < revision and k in (study.evidence_json or {}))


def sync_revisions(study, plan) -> list[str]:
    """Stamp every unstamped artifact with the current revision, and move any stale one to history.

    Every revision change invalidates its dependents at once, so an artifact present without a stamp
    was computed under the current revision. Returns the keys moved as stale."""
    revision = current_revision(plan)
    if revision is None:
        return []
    evidence = study.evidence_json or {}
    stale = stale_artifacts(study, plan)
    moved: list[str] = []
    if stale:
        stamps = evidence.get("artifact_revisions") or {}
        moved = _move(study, plan, set(stale), revision=min(stamps[k] for k in stale), why=["stale"])
        evidence = study.evidence_json or {}
    stamps = dict(evidence.get("artifact_revisions") or {})
    fresh = {k: revision for k in REVISION_SCOPED if k in evidence and k not in stamps}
    if fresh:
        study.evidence_json = {**evidence, "artifact_revisions": {**stamps, **fresh}}
    return moved
