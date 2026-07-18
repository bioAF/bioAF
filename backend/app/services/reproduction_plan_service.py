"""Service for the ReproductionPlan aggregate (lit_validation B2/B3 output, C1 input).

Persists the plan produced by the extractor and its ComparisonTargets, and reads it back org-scoped
through the owning study. The extractor calls create_plan + add_comparison_targets; the C1 gate and
the result views read via get_plan.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.comparison_target import ComparisonTarget
from app.models.reproduction_plan import ReproductionPlan
from app.models.validation_study import ValidationStudy
from app.services.audit_service import log_action


class ReproductionPlanService:
    @staticmethod
    async def create_plan(
        session: AsyncSession,
        study: ValidationStudy,
        user_id: int,
        *,
        accessions: list | None = None,
        sample_sheet: dict | None = None,
        pipeline_key: str | None = None,
        pipeline_version: str | None = None,
        parameters: dict | None = None,
        reference_genome: str | None = None,
        reference_build: str | None = None,
        mapping_confidence: str | None = None,
        mapping_notes: str | None = None,
        blockers: list | None = None,
        extractor_model: str | None = None,
        extractor_provider: str | None = None,
    ) -> ReproductionPlan:
        """Create a plan for ``study`` and point the study at it (its current plan). Audited."""
        plan = ReproductionPlan(
            validation_study_id=study.id,
            accessions_json=accessions if accessions is not None else [],
            sample_sheet_json=sample_sheet,
            pipeline_key=pipeline_key,
            pipeline_version=pipeline_version,
            parameters_json=parameters,
            reference_genome=reference_genome,
            reference_build=reference_build,
            mapping_confidence=mapping_confidence,
            mapping_notes=mapping_notes,
            blockers_json=blockers if blockers is not None else [],
            extractor_model=extractor_model,
            extractor_provider=extractor_provider,
        )
        session.add(plan)
        await session.flush()

        study.reproduction_plan_id = plan.id
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="reproduction_plan",
            entity_id=plan.id,
            action="create",
            details={
                "validation_study_id": study.id,
                "pipeline_key": pipeline_key,
                "mapping_confidence": mapping_confidence,
                "accession_count": len(plan.accessions_json or []),
            },
        )
        return plan

    @staticmethod
    async def add_comparison_targets(
        session: AsyncSession, plan: ReproductionPlan, targets: list[dict]
    ) -> list[ComparisonTarget]:
        """Attach the paper's quantitative claims (B2d) to ``plan`` as ComparisonTargets."""
        created: list[ComparisonTarget] = []
        for t in targets:
            metric_key = (t.get("metric_key") or "").strip()
            if not metric_key:
                continue
            target = ComparisonTarget(
                reproduction_plan_id=plan.id,
                metric_key=metric_key,
                claimed_value=t.get("claimed_value"),
                unit=t.get("unit"),
                tolerance=t.get("tolerance"),
                source_locator=t.get("source_locator"),
            )
            session.add(target)
            created.append(target)
        await session.flush()
        return created

    @staticmethod
    async def get_plan(session: AsyncSession, study_id: int, org_id: int) -> ReproductionPlan | None:
        """Load the study's current plan (with its targets), scoped to the org via the study."""
        study = (
            await session.execute(
                select(ValidationStudy).where(
                    ValidationStudy.id == study_id,
                    ValidationStudy.organization_id == org_id,
                )
            )
        ).scalar_one_or_none()
        if not study or not study.reproduction_plan_id:
            return None
        return (
            await session.execute(
                select(ReproductionPlan)
                .where(ReproductionPlan.id == study.reproduction_plan_id)
                .options(selectinload(ReproductionPlan.comparison_targets))
            )
        ).scalar_one_or_none()
