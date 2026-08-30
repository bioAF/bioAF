"""Service for the ValidationStudy aggregate (lit_validation A1).

Owns creation and the state-machine-guarded, audited transitions. The transition guard mirrors the
Experiment/Sample ``update_status`` convention (same "Cannot transition ... Next valid status" error
shape) and delegates the allowed edges to ``app.models.validation_study``.
"""

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.validation_study import (
    VALIDATION_STUDY_CLASSIFICATIONS,
    ValidationStudy,
    can_transition,
    next_states,
)
from app.services.audit_service import log_action
from app.services.pipeline_mapper import is_library_strategy_conflict
from app.services.reproduction_plan_service import ReproductionPlanService


async def _has_runnable_samples(session: AsyncSession, experiment_id: int | None) -> bool:
    """Whether the study's experiment holds a sample with a linked input file.

    The same definition the driver uses for "what becomes the analysis matrix": a sample with no
    FASTQ is not something the analysis can be relaunched against.
    """
    if experiment_id is None:
        return False
    from app.models.sample import Sample, sample_files

    row = (
        await session.execute(
            select(Sample.id)
            .join(sample_files, Sample.id == sample_files.c.sample_id)
            .where(Sample.experiment_id == experiment_id)
        )
    ).first()
    return row is not None


class ValidationStudyService:
    @staticmethod
    async def create_study(
        session: AsyncSession,
        org_id: int,
        user_id: int,
        *,
        paper_id: int | None = None,
        source_doi: str | None = None,
        source_accession: str | None = None,
    ) -> ValidationStudy:
        """Create a study in the initial ``requested`` state, with an audited create."""
        study = ValidationStudy(
            organization_id=org_id,
            requested_by_user_id=user_id,
            paper_id=paper_id,
            source_doi=source_doi,
            source_accession=source_accession,
            state="requested",
        )
        session.add(study)
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="validation_study",
            entity_id=study.id,
            action="create",
            details={"state": "requested", "paper_id": paper_id},
        )
        return study

    @staticmethod
    async def list_studies(session: AsyncSession, org_id: int) -> list[ValidationStudy]:
        """All of an org's validation studies, newest first (for the list surface)."""
        result = await session.execute(
            select(ValidationStudy).where(ValidationStudy.organization_id == org_id).order_by(ValidationStudy.id.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def retry_study(
        session: AsyncSession,
        study_id: int,
        org_id: int,
        user_id: int,
    ) -> ValidationStudy:
        """Send an errored study back to the furthest point its existing work supports. Audited.

        `error` means the infrastructure failed, not that the paper did: a wrong launch parameter, a
        dead node, an unreachable reference. The model has called it "retryable" in a comment since
        it was written while the transition table said otherwise, so the only ways out were a
        hand-edited row or re-running a fetch that had already succeeded. Demo run 42 is the case
        this exists for: 122 GB acquired, then every alignment rejected an index that did not match
        the fasta beside it.

        Where it resumes is decided by what survived, never by the caller:

        - **Fetched samples with FASTQ** -> `setup`, which relaunches the analysis against them. The
          expensive half is already paid for.
        - **Nothing fetched** -> `plan_ready`, the C1 gate. Re-fetching spends real money, so a human
          approves it deliberately.

        The previous attempt's `analysis_run_id` and its Level-3 session are cleared. Left in place,
        the driver reads the FAILED run as this attempt's result and reproduces from a session that
        no longer applies.
        """
        study = (
            await session.execute(
                select(ValidationStudy).where(
                    ValidationStudy.id == study_id,
                    ValidationStudy.organization_id == org_id,
                )
            )
        ).scalar_one_or_none()
        if not study:
            raise HTTPException(404, "Validation study not found")
        if study.state != "error":
            raise HTTPException(
                400,
                f"Only a study in 'error' can be retried; this one is in '{study.state}'.",
            )

        resumable = await _has_runnable_samples(session, study.experiment_id)
        target = "setup" if resumable else "plan_ready"

        study.analysis_run_id = None
        evidence = dict(study.evidence_json or {})
        for key in ("level3_run_session_id", "level3", "qc", "acquire_retry_at"):
            evidence.pop(key, None)
        study.evidence_json = evidence or None
        study.failure_reason = None
        await session.flush()

        return await ValidationStudyService.transition(session, study_id, org_id, user_id, target)

    @staticmethod
    async def transition(
        session: AsyncSession,
        study_id: int,
        org_id: int,
        user_id: int,
        new_state: str,
        *,
        classification: str | None = None,
        failure_reason: str | None = None,
    ) -> ValidationStudy:
        """Move a study to ``new_state`` if the transition is allowed; audited.

        Reaching the terminal ``classified`` state requires a valid classification (one of the six
        buckets). ``failure_reason`` is recorded when transitioning to ``error``.
        """
        study = (
            await session.execute(
                select(ValidationStudy).where(
                    ValidationStudy.id == study_id,
                    ValidationStudy.organization_id == org_id,
                )
            )
        ).scalar_one_or_none()
        if not study:
            raise HTTPException(404, "Validation study not found")

        allowed = next_states(study.state)
        if new_state not in allowed:
            raise HTTPException(
                400,
                f"Cannot transition from '{study.state}' to '{new_state}'. "
                f"Next valid status: {', '.join(allowed) if allowed else 'none (terminal state)'}.",
            )

        if new_state == "classified":
            if classification not in VALIDATION_STUDY_CLASSIFICATIONS:
                raise HTTPException(
                    400,
                    f"A classification is required to reach 'classified'; got {classification!r}. "
                    f"One of: {', '.join(VALIDATION_STUDY_CLASSIFICATIONS)}.",
                )
            study.classification = classification

        if new_state == "error" and failure_reason is not None:
            study.failure_reason = failure_reason

        old_state = study.state
        study.state = new_state
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="validation_study",
            entity_id=study.id,
            action="state_change",
            details={"state": new_state, "classification": study.classification},
            previous_value={"state": old_state},
        )
        return study

    @staticmethod
    async def _load(session: AsyncSession, study_id: int, org_id: int) -> ValidationStudy:
        study = (
            await session.execute(
                select(ValidationStudy).where(
                    ValidationStudy.id == study_id,
                    ValidationStudy.organization_id == org_id,
                )
            )
        ).scalar_one_or_none()
        if not study:
            raise HTTPException(404, "Validation study not found")
        return study

    @staticmethod
    async def approve_plan(session: AsyncSession, study_id: int, org_id: int, user_id: int) -> ValidationStudy:
        """C1 gate: ratify the plan and advance plan_ready -> acquiring_data, stamping the approver."""
        study = await ValidationStudyService._load(session, study_id, org_id)
        if not can_transition(study.state, "acquiring_data"):
            raise HTTPException(
                400,
                f"Cannot approve a plan from '{study.state}'; the study must be in 'plan_ready'.",
            )

        # The one blocker that refuses rather than advises. Every other blocker a plan carries is
        # information for the scientist ratifying it; this one says the plan names a pipeline that
        # cannot read the data the study is scoped to, and approving is what spends the money.
        # Recorded by the extractor, which is where the deposit was read; re-deriving it here would
        # put a network fetch in the way of every approval and let an outage decide the answer.
        plan = await ReproductionPlanService.get_plan(session, study_id, org_id)
        conflict = next(
            (b for b in ((plan.blockers_json if plan else None) or []) if is_library_strategy_conflict(b)), None
        )
        if conflict:
            raise HTTPException(400, conflict)

        study.approved_by_user_id = user_id
        study.approved_at = datetime.now(timezone.utc)
        old_state = study.state
        study.state = "acquiring_data"
        await session.flush()
        await log_action(
            session,
            user_id=user_id,
            entity_type="validation_study",
            entity_id=study.id,
            action="plan_approved",
            details={"state": "acquiring_data"},
            previous_value={"state": old_state},
        )
        return study

    @staticmethod
    async def classify_by_hand(
        session: AsyncSession, study_id: int, org_id: int, user_id: int, classification: str
    ) -> ValidationStudy:
        """Manual comparison gate: a human ratifies the computed-vs-claimed evidence and records the
        terminal classification (comparing -> classified). Phase 1 keeps this comparison manual; the
        automatic classifier (E4) supersedes it later. The classification must be one of the buckets."""
        study = await ValidationStudyService._load(session, study_id, org_id)
        if not can_transition(study.state, "classified"):
            raise HTTPException(
                400,
                f"Cannot classify from '{study.state}'; the study must be in 'comparing'.",
            )
        if classification not in VALIDATION_STUDY_CLASSIFICATIONS:
            raise HTTPException(
                400,
                f"Invalid classification {classification!r}. One of: {', '.join(VALIDATION_STUDY_CLASSIFICATIONS)}.",
            )
        old_state = study.state
        study.state = "classified"
        study.classification = classification
        await session.flush()
        await log_action(
            session,
            user_id=user_id,
            entity_type="validation_study",
            entity_id=study.id,
            action="classified_by_hand",
            details={"classification": classification},
            previous_value={"state": old_state},
        )
        return study

    @staticmethod
    async def decline_plan(
        session: AsyncSession, study_id: int, org_id: int, user_id: int, reason: str | None = None
    ) -> ValidationStudy:
        """C1 gate: reject the plan (terminal plan_declined). ``reason`` is recorded on the study."""
        study = await ValidationStudyService._load(session, study_id, org_id)
        if not can_transition(study.state, "plan_declined"):
            raise HTTPException(
                400,
                f"Cannot decline a plan from '{study.state}'; the study must be in 'plan_ready'.",
            )
        if reason:
            study.failure_reason = reason
        old_state = study.state
        study.state = "plan_declined"
        await session.flush()
        await log_action(
            session,
            user_id=user_id,
            entity_type="validation_study",
            entity_id=study.id,
            action="plan_declined",
            details={"reason": reason},
            previous_value={"state": old_state},
        )
        return study
