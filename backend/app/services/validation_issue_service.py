"""plan_7 step 14a/14c: recording and reading the steps that hit an error validating a paper.

Every caller of ``llm_decision.decide`` hands its failures here as ``decision.as_issue(impact=...)``
rows. The service is deliberately thin: it validates the controlled vocabulary, drops the ``None``
that a successful call produces, and reads the list back in occurrence order for the report and the
provenance export.

Nothing here can fail a study. A step that could not record why it failed is worse off than one that
did, but it is not a reason to stop validating the paper.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.validation_study import ValidationStudy
from app.models.validation_study_issue import ISSUE_IMPACTS, ISSUE_OUTCOMES, ValidationStudyIssue

logger = logging.getLogger("bioaf.validation_issues")


class ValidationIssueService:
    @staticmethod
    async def record(session: AsyncSession, study: ValidationStudy, rows: Iterable[dict | None]) -> int:
        """Append issue rows for ``study``. Returns how many were stored.

        ``None`` entries are expected and skipped: callers append ``decision.as_issue(...)``
        unconditionally and it is None when the call succeeded.

        One row per occurrence, including the same step twice. A re-ask that failed again is a
        second occurrence, and collapsing the two would hide that the retry was spent.
        """
        stored = 0
        for row in rows or []:
            if not row:
                continue
            outcome = str(row.get("outcome") or "")
            impact = str(row.get("impact") or "")
            step = str(row.get("step") or "").strip()
            if outcome not in ISSUE_OUTCOMES or impact not in ISSUE_IMPACTS or not step:
                # The vocabulary is what the report renders from; a value outside it would render as
                # nothing at all, which is worse than not recording it.
                logger.warning("discarding an unrecognised validation issue row: %s", row)
                continue
            session.add(
                ValidationStudyIssue(
                    validation_study_id=study.id,
                    step=step[:200],
                    outcome=outcome,
                    impact=impact,
                    message=str(row.get("message") or "").strip(),
                    model=(str(row["model"])[:120] if row.get("model") else None),
                )
            )
            stored += 1
        if stored:
            await session.flush()
        return stored

    @staticmethod
    async def list_for_study(session: AsyncSession, study_id: int, org_id: int) -> list[dict]:
        """The study's issues in occurrence order, org-scoped through the study."""
        study = (
            await session.execute(
                select(ValidationStudy.id).where(
                    ValidationStudy.id == study_id, ValidationStudy.organization_id == org_id
                )
            )
        ).scalar_one_or_none()
        if study is None:
            return []
        rows = (
            (
                await session.execute(
                    select(ValidationStudyIssue)
                    .where(ValidationStudyIssue.validation_study_id == study_id)
                    .order_by(ValidationStudyIssue.id)
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "step": r.step,
                "outcome": r.outcome,
                "impact": r.impact,
                "message": r.message,
                "model": r.model,
                "at": r.occurred_at.isoformat() if r.occurred_at else None,
            }
            for r in rows
        ]
