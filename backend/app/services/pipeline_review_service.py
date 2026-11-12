"""Pipeline review service: handles review creation with all transactional side effects."""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.exceptions import NotFoundError, UnprocessableError
from app.models.experiment import Experiment
from app.models.pipeline_run import PipelineRun, PipelineRunSample
from app.models.pipeline_run_review import PipelineRunReview
from app.models.sample import Sample
from app.services.audit_service import log_action
from app.services.event_bus import event_bus
from app.services.event_types import PIPELINE_RUN_REVIEWED
from app.runtime import spawn_event

logger = logging.getLogger("bioaf.pipeline_review")

# One message for every bad sample reference, so a caller cannot tell a sample in another
# Organization from one that does not exist.
_BAD_SAMPLE_REFERENCE = "Sample verdicts must reference samples in this pipeline run"


async def _scoped_run(session: AsyncSession, pipeline_run_id: int, org_id: int) -> PipelineRun:
    """The run, only when it belongs to ``org_id``; a foreign run is the same 404 as a missing one."""
    result = await session.execute(
        select(PipelineRun)
        .options(selectinload(PipelineRun.experiment))
        .where(PipelineRun.id == pipeline_run_id, PipelineRun.organization_id == org_id)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise NotFoundError("Pipeline run not found")
    return run


async def _active_review(session: AsyncSession, pipeline_run_id: int) -> PipelineRunReview | None:
    """The active review of a run the caller has already scoped."""
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


async def _verdict_samples(
    session: AsyncSession, run: PipelineRun, org_id: int, sample_verdicts: dict
) -> dict[int, Sample]:
    """Resolve every sample a review's verdicts name, or refuse the whole request.

    A sample must be a member of the run (``pipeline_run_samples``) and owned by ``org_id``
    through its experiment. Membership alone is not enough: a corrupt association to another
    Organization's sample must not let a review change it. Nothing is skipped, so a mixed
    payload never half-applies.
    """
    ids: set[int] = set()
    for key in sample_verdicts:
        try:
            sample_id = int(key)
        except (TypeError, ValueError):
            raise UnprocessableError(_BAD_SAMPLE_REFERENCE) from None
        if str(sample_id) != key or sample_id in ids:
            raise UnprocessableError(_BAD_SAMPLE_REFERENCE)
        ids.add(sample_id)
    result = await session.execute(
        select(Sample)
        .join(PipelineRunSample, PipelineRunSample.sample_id == Sample.id)
        .join(Experiment, Experiment.id == Sample.experiment_id)
        .where(
            Sample.id.in_(ids),
            PipelineRunSample.pipeline_run_id == run.id,
            Experiment.organization_id == org_id,
        )
    )
    samples = {sample.id: sample for sample in result.scalars().unique()}
    if set(samples) != ids:
        raise UnprocessableError(_BAD_SAMPLE_REFERENCE)
    return samples


class PipelineReviewService:
    @staticmethod
    async def create_review(
        session: AsyncSession,
        pipeline_run_id: int,
        org_id: int,
        reviewer_user_id: int,
        verdict: str,
        notes: str | None,
        sample_verdicts: dict | None,
        recommended_exclusions: list[int] | None,
    ) -> PipelineRunReview:
        """Create a review with all side effects in a single transaction.

        The run must belong to ``org_id`` (404 otherwise) and every sample verdict must name a
        member sample owned by ``org_id`` (422 otherwise). Both are checked before any write.

        Side effects:
        1. Supersede existing active review (if any)
        2. Create the new review record
        3. Update sample QC flags from sample_verdicts
        4. Write audit log entry
        5. Transition experiment from pipeline_complete → reviewed
        6. Emit notification event
        """
        run = await _scoped_run(session, pipeline_run_id, org_id)
        samples = await _verdict_samples(session, run, org_id, sample_verdicts) if sample_verdicts else {}

        # 1. Supersede existing active review
        active_review = await _active_review(session, pipeline_run_id)

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
                sample = samples[int(sample_id_str)]
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

        # 5. Update experiment status, only for an experiment of the same Organization
        if (
            run.experiment
            and run.experiment.organization_id == org_id
            and verdict in ("approved", "approved_with_caveats")
        ):
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
            spawn_event(
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
                ),
                event_type=PIPELINE_RUN_REVIEWED,
            )

        return review

    @staticmethod
    async def get_active_review(
        session: AsyncSession, pipeline_run_id: int, *, org_id: int
    ) -> PipelineRunReview | None:
        """Get the active (non-superseded) review of a run in ``org_id``; None when it has none."""
        await _scoped_run(session, pipeline_run_id, org_id)
        return await _active_review(session, pipeline_run_id)

    @staticmethod
    async def list_reviews(session: AsyncSession, pipeline_run_id: int, *, org_id: int) -> list[PipelineRunReview]:
        """List all reviews of a run in ``org_id``, newest first."""
        await _scoped_run(session, pipeline_run_id, org_id)
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
        """Find completed pipeline runs with no active review older than threshold.

        Deliberately crosses Organizations: the background reminder scan emits each event with
        its run's own org. It is not an API lookup and must not be used as one.
        """
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
