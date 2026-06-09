"""Pipeline review service — handles review creation with all transactional side effects."""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.exceptions import NotFoundError
from app.models.pipeline_run import PipelineRun
from app.models.pipeline_run_review import PipelineRunReview
from app.models.sample import Sample
from app.services.audit_service import log_action
from app.services.event_bus import event_bus
from app.services.event_types import PIPELINE_RUN_REVIEWED

logger = logging.getLogger("bioaf.pipeline_review")


class PipelineReviewService:
    @staticmethod
    async def create_review(
        session: AsyncSession,
        pipeline_run_id: int,
        reviewer_user_id: int,
        verdict: str,
        notes: str | None,
        sample_verdicts: dict | None,
        recommended_exclusions: list[int] | None,
    ) -> PipelineRunReview:
        """Create a review with all side effects in a single transaction.

        Side effects:
        1. Supersede existing active review (if any)
        2. Create the new review record
        3. Update sample QC flags from sample_verdicts
        4. Write audit log entry
        5. Transition experiment from pipeline_complete → reviewed
        6. Emit notification event
        """
        # Load pipeline run with relationships
        run = await session.execute(
            select(PipelineRun)
            .options(selectinload(PipelineRun.experiment), selectinload(PipelineRun.samples))
            .where(PipelineRun.id == pipeline_run_id)
        )
        run = run.scalar_one_or_none()
        if not run:
            raise NotFoundError("Pipeline run not found")

        # 1. Supersede existing active review
        active_review = await PipelineReviewService.get_active_review(session, pipeline_run_id)

        # 2. Create new review
        review = PipelineRunReview(
            pipeline_run_id=pipeline_run_id,
            reviewer_user_id=reviewer_user_id,
            verdict=verdict,
            notes=notes,
            sample_verdicts_json=sample_verdicts,
            recommended_exclusions=recommended_exclusions,
            reviewed_at=datetime.now(timezone.utc),
        )
        session.add(review)
        await session.flush()

        # Set superseded_by on old review (must happen after flush to have review.id)
        if active_review:
            active_review.superseded_by_id = review.id
            await session.flush()

        # 3. Update sample QC flags
        if sample_verdicts:
            for sample_id_str, verdict_data in sample_verdicts.items():
                try:
                    sample_id = int(sample_id_str)
                except (ValueError, TypeError):
                    continue

                result = await session.execute(select(Sample).where(Sample.id == sample_id))
                sample = result.scalar_one_or_none()
                if not sample:
                    continue

                new_qc = verdict_data.get("verdict") if isinstance(verdict_data, dict) else None
                new_notes = verdict_data.get("notes") if isinstance(verdict_data, dict) else None

                if new_qc and new_qc != sample.qc_status:
                    previous_qc = {"qc_status": sample.qc_status, "qc_notes": sample.qc_notes}
                    sample.qc_status = new_qc
                    if new_notes is not None:
                        sample.qc_notes = new_notes
                    await session.flush()

                    await log_action(
                        session,
                        user_id=reviewer_user_id,
                        entity_type="sample",
                        entity_id=sample.id,
                        action="qc_update",
                        details={"qc_status": new_qc, "qc_notes": new_notes, "via": "pipeline_review"},
                        previous_value=previous_qc,
                    )

        # 4. Audit log for the review itself
        await log_action(
            session,
            user_id=reviewer_user_id,
            entity_type="pipeline_run_review",
            entity_id=review.id,
            action="created",
            details={
                "pipeline_run_id": pipeline_run_id,
                "verdict": verdict,
                "superseded_review_id": active_review.id if active_review else None,
            },
        )

        # 5. Update experiment status
        if run.experiment and verdict in ("approved", "approved_with_caveats"):
            experiment = run.experiment
            if experiment.status == "pipeline_complete":
                old_status = experiment.status
                experiment.status = "reviewed"
                await session.flush()

                await log_action(
                    session,
                    user_id=reviewer_user_id,
                    entity_type="experiment",
                    entity_id=experiment.id,
                    action="status_change",
                    details={
                        "status": "reviewed",
                        "via": "pipeline_review",
                        "pipeline_run_id": pipeline_run_id,
                        "reviewer_user_id": reviewer_user_id,
                        "verdict": verdict,
                        "notes": notes,
                    },
                    previous_value={"status": old_status},
                )

        # 6. Emit notification event (fire-and-forget after commit)
        if run.experiment:
            asyncio.create_task(
                event_bus.emit(
                    PIPELINE_RUN_REVIEWED,
                    {
                        "event_type": PIPELINE_RUN_REVIEWED,
                        "org_id": run.organization_id,
                        "user_id": reviewer_user_id,
                        "entity_type": "pipeline_run_review",
                        "entity_id": review.id,
                        "title": f"Pipeline run reviewed: {verdict}",
                        "message": notes or "",
                        "summary": f"Pipeline run {pipeline_run_id} reviewed as '{verdict}'",
                        "metadata": {
                            "pipeline_run_id": pipeline_run_id,
                            "experiment_id": run.experiment_id,
                            "verdict": verdict,
                        },
                    },
                )
            )

        return review

    @staticmethod
    async def get_active_review(session: AsyncSession, pipeline_run_id: int) -> PipelineRunReview | None:
        """Get the active (non-superseded) review for a pipeline run."""
        result = await session.execute(
            select(PipelineRunReview)
            .options(selectinload(PipelineRunReview.reviewer))
            .where(
                PipelineRunReview.pipeline_run_id == pipeline_run_id,
                PipelineRunReview.superseded_by_id.is_(None),
            )
            .order_by(PipelineRunReview.reviewed_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_reviews(session: AsyncSession, pipeline_run_id: int) -> list[PipelineRunReview]:
        """List all reviews for a pipeline run, newest first."""
        result = await session.execute(
            select(PipelineRunReview)
            .options(selectinload(PipelineRunReview.reviewer))
            .where(PipelineRunReview.pipeline_run_id == pipeline_run_id)
            .order_by(PipelineRunReview.reviewed_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_unreviewed_runs(
        session: AsyncSession,
        hours_threshold: int = 72,
    ) -> list[PipelineRun]:
        """Find completed pipeline runs with no active review older than threshold."""
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_threshold)

        # Subquery for runs that have active reviews
        reviewed_run_ids = (
            select(PipelineRunReview.pipeline_run_id).where(PipelineRunReview.superseded_by_id.is_(None)).distinct()
        )

        result = await session.execute(
            select(PipelineRun)
            .options(selectinload(PipelineRun.experiment))
            .where(
                PipelineRun.status == "completed",
                PipelineRun.completed_at.isnot(None),
                PipelineRun.completed_at < cutoff,
                PipelineRun.id.notin_(reviewed_run_ids),
            )
        )
        return list(result.scalars().all())
