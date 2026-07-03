"""A2 orchestration driver for the comprehension half (lit_validation).

Advances a study from ``requested`` through the reading stage: acquires full text (B1; here the text
is supplied), runs the B2/B3 extractor, and then either parks at ``plan_ready`` for the C1 human gate
or takes an early-exit terminal classification when the paper is not reproducible before any compute.

The early-exit rules here are the reading-stage subset of the eventual classifier (E4): no accession
-> missing_data; no nf-core equivalent -> not_reproducible. The full comparison-driven classifier
lands with E2/E3/E4. The back-half driver (acquiring_data -> ... -> classified) is a later increment
and belongs on the event bus / lifespan loop like pipeline-monitor and auto-run.
"""

from app.exceptions import ValidationError
from app.models.reproduction_plan import ReproductionPlan
from app.models.validation_study import ValidationStudy
from app.services.validation_extraction_service import ValidationExtractionService
from app.services.validation_study_service import ValidationStudyService

from sqlalchemy.ext.asyncio import AsyncSession


def _early_exit_classification(plan: ReproductionPlan) -> str | None:
    """Reading-stage early exit, or None to proceed to plan_ready.

    Order matters: a missing accession is the harder stop (no data to run at all) and is checked
    before pipeline mappability. missing_methods is folded into not_reproducible for now; it is split
    out when the full classifier (E4) lands.
    """
    if not (plan.accessions_json or []):
        return "missing_data"
    if plan.pipeline_key is None:
        return "not_reproducible"
    return None


class ValidationDriverService:
    @staticmethod
    async def read_and_plan(
        session: AsyncSession,
        study: ValidationStudy,
        full_text: str,
        org_id: int,
        user_id: int,
    ) -> ValidationStudy:
        """Drive a requested study to plan_ready (or an early-exit classification)."""
        if study.state != "requested":
            raise ValidationError(
                f"read_and_plan can only start from 'requested'; study is in '{study.state}'."
            )

        # B1 full-text acquisition is represented by the acquiring_text stage; the text is supplied
        # here (a paste-in or an upstream fetch), so we just move through the stage.
        study = await ValidationStudyService.transition(session, study.id, org_id, user_id, "acquiring_text")
        study = await ValidationStudyService.transition(session, study.id, org_id, user_id, "reading")

        plan = await ValidationExtractionService.extract(session, study, full_text, org_id, user_id)

        classification = _early_exit_classification(plan)
        if classification is not None:
            # Record the "why" (the plan's blockers) before the terminal transition.
            blockers = plan.blockers_json or []
            if blockers:
                study.failure_reason = "; ".join(blockers)
            return await ValidationStudyService.transition(
                session, study.id, org_id, user_id, "classified", classification=classification
            )

        return await ValidationStudyService.transition(session, study.id, org_id, user_id, "plan_ready")
