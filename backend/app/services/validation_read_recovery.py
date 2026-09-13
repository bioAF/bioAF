"""plan_8_1 section 1.4, decision D6: recover the studies whose read failed before bioAF could say so.

Studies 42 and 43 were classified ``missing_data`` by a read cut off at the model's output limit. Nothing
could recover them: Retry accepts only ``error``, and a classified study is terminal. This moves every
``classified`` study that the failed-read rule (``validation_read_failure.read_failure``) matches to
``error``, audited, keeping its prior state, classification and failure reason in history. Retry then
reads it again.

**A product rule, not an edit to two rows.** It selects with the same function every surface renders
with, so it moves nothing the rule does not match. It runs as a data migration (``alembic 142``) on a
synchronous connection.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from app.models.audit_log import AuditLog
from app.models.comparison_target import ComparisonTarget
from app.models.validation_study import ValidationStudy
from app.models.validation_study_issue import ValidationStudyIssue
from app.services.validation_read_failure import PAPER_READING_STEP, read_failure, read_failure_blocker

_STUDIES = ValidationStudy.__table__
_ISSUES = ValidationStudyIssue.__table__
_TARGETS = ComparisonTarget.__table__
_AUDIT = AuditLog.__table__


def recover_failed_reads(connection) -> list[int]:
    """Move every matching ``classified`` study to ``error``. Returns the ids it moved."""
    now = datetime.now(timezone.utc).isoformat()
    moved: list[int] = []
    studies = connection.execute(
        select(
            _STUDIES.c.id,
            _STUDIES.c.evidence_json,
            _STUDIES.c.reproduction_plan_id,
            _STUDIES.c.classification,
            _STUDIES.c.failure_reason,
            _STUDIES.c.requested_by_user_id,
        ).where(_STUDIES.c.state == "classified")
    ).all()
    for study in studies:
        issues = [
            {"step": row.step, "outcome": row.outcome, "impact": row.impact}
            for row in connection.execute(
                select(_ISSUES.c.step, _ISSUES.c.outcome, _ISSUES.c.impact).where(
                    _ISSUES.c.validation_study_id == study.id, _ISSUES.c.step == PAPER_READING_STEP
                )
            ).all()
        ]
        claims = 0
        if study.reproduction_plan_id:
            claims = connection.execute(
                select(func.count(_TARGETS.c.id)).where(_TARGETS.c.reproduction_plan_id == study.reproduction_plan_id)
            ).scalar_one()
        evidence = dict(study.evidence_json or {})
        failure = read_failure(evidence, issues=issues, claim_count=claims)
        if failure is None:
            continue
        reason = read_failure_blocker(failure["cause"])
        evidence["recovery_history"] = list(evidence.get("recovery_history") or []) + [
            {
                "at": now,
                "rule": "legacy" if failure["legacy"] else "extraction_provenance",
                "prior_state": "classified",
                "prior_classification": study.classification,
                "prior_failure_reason": study.failure_reason,
                "cause": failure["cause"],
            }
        ]
        evidence["error_at"] = now
        connection.execute(
            _STUDIES.update()
            .where(_STUDIES.c.id == study.id, _STUDIES.c.state == "classified")
            .values(state="error", classification=None, failure_reason=reason, evidence_json=evidence)
        )
        connection.execute(
            _AUDIT.insert().values(
                user_id=None,
                entity_type="validation_study",
                entity_id=study.id,
                action="state_change",
                details_json={
                    "state": "error",
                    "classification": None,
                    "recovered_failed_read": True,
                    "cause": failure["cause"],
                    "decision": "plan_8_1 D6",
                },
                previous_value_json={
                    "state": "classified",
                    "classification": study.classification,
                    "failure_reason": study.failure_reason,
                },
            )
        )
        moved.append(study.id)
    return moved
