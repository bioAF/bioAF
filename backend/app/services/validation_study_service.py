"""Service for the ValidationStudy aggregate (lit_validation A1).

Owns creation and the state-machine-guarded, audited transitions. The transition guard mirrors the
Experiment/Sample ``update_status`` convention (same "Cannot transition ... Next valid status" error
shape) and delegates the allowed edges to ``app.models.validation_study``.
"""

import logging
from datetime import datetime, timedelta, timezone

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
from app.services.event_bus import event_bus
from app.services.event_types import VALIDATION_STUDY_ERROR
from app.services.pipeline_mapper import deposit_conflict
from app.services.reproduction_plan_service import ReproductionPlanService

logger = logging.getLogger("bioaf.validation_study")

# How long a stopped study's fetched data is kept so a retry can reuse it.
#
# Longer than the Nextflow work dir's two days (`work_dir_reaper`), because that data is
# recomputable and this is not: re-fetching a study is a paid, multi-hour download from ENA. Short
# enough that a study nobody came back to stops being billed. `validation_fetch_reaper` acts on the
# deadline this constant sets; the API hands the deadline itself to the UI, so the window is decided
# here once rather than restated in the frontend.
VALIDATION_FETCH_RETENTION_DAYS = 3


def _study_label(study: ValidationStudy) -> str:
    """What to call the study in a notification: what it reproduces, never a bare id."""
    return study.source_doi or study.source_accession or f"Study #{study.id}"


async def record_study_error(study: ValidationStudy) -> None:
    """Stamp a study that has just stopped, and tell the person who asked for it.

    Two things a stopped study owes a human. **When it stopped**, which is what dates the retry
    window: nothing else on the row records it (``updated_at`` moves for any write), and the reaper
    needs a time it can prove. And **that it stopped at all**, because `error` is an infrastructure
    failure with a manual way back, and the only account of one used to be a badge on a page nobody
    had open.

    The notification is best-effort: recording the failure is the point, announcing it is on top.
    """
    now = datetime.now(timezone.utc)
    evidence = dict(study.evidence_json or {})
    evidence["error_at"] = now.isoformat()
    evidence["fetch_reap_after"] = (now + timedelta(days=VALIDATION_FETCH_RETENTION_DAYS)).isoformat()
    # A fresh dict, because evidence_json is a plain (non-Mutable) JSONB column and an in-place
    # mutation of the same reference goes untracked.
    study.evidence_json = evidence

    label = _study_label(study)
    reason = study.failure_reason or "the reproduction could not be completed"
    try:
        await event_bus.emit(
            VALIDATION_STUDY_ERROR,
            {
                "event_type": VALIDATION_STUDY_ERROR,
                "org_id": study.organization_id,
                "user_id": study.requested_by_user_id,
                "target_user_id": study.requested_by_user_id,
                "entity_type": "validation_study",
                "entity_id": study.id,
                "title": "A validation study stopped on a technical failure",
                "message": (
                    f"{label} stopped: {reason}. This is not a result about the paper. Retry it within "
                    f"{VALIDATION_FETCH_RETENTION_DAYS} days and the data already downloaded is reused; "
                    "after that the data is deleted and a retry downloads it again."
                ),
                "severity": "warning",
                "summary": f"Validation study {study.id} stopped: {reason}",
            },
        )
    except Exception:
        logger.exception("validation study %d: could not announce the error", study.id)


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
        # `error_at` / `fetch_reap_after` are the retry window, and a retry is what that window was
        # waiting for. Left in place, a study back in flight still carries a countdown against data
        # it is actively using.
        for key in ("level3_run_session_id", "level3", "qc", "acquire_retry_at", "error_at", "fetch_reap_after"):
            evidence.pop(key, None)
        if not resumable:
            # Landing at `plan_ready` is what makes the re-fetch a decision rather than a side
            # effect of clicking Retry, and today that intent lives only in the target state. Say it
            # on the study instead: the C1 gate warns that approving downloads the data again, and
            # anything that later advances `plan_ready` on its own has something to refuse on.
            evidence["awaiting_refetch_approval"] = True
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
        if new_state == "error":
            await record_study_error(study)
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
    async def approve_plan(
        session: AsyncSession, study_id: int, org_id: int, user_id: int, *, route: str = "pipeline"
    ) -> ValidationStudy:
        """C1 gate: ratify the plan and start the chosen route, stamping the approver.

        ``pipeline`` (the default) advances to ``acquiring_data`` and re-runs the paper from its raw
        reads, which is the historical behaviour. ``deposit`` advances to ``acquiring_processed`` and
        starts from the pre-processed data the authors published.

        The route is recorded on the study's evidence rather than on the plan, because it is a
        property of THIS attempt: the same plan can be tried both ways.
        """
        study = await ValidationStudyService._load(session, study_id, org_id)
        target = "acquiring_processed" if route == "deposit" else "acquiring_data"
        if not can_transition(study.state, target):
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
        conflict = deposit_conflict(plan.blockers_json if plan else None, plan.library_strategy if plan else None)
        # Two ways past it, both deliberate: re-point the plan at the pipeline the deposit names
        # (which clears the blocker), or record why the deposit itself is wrong. Neither is implicit.
        if conflict and not (study.evidence_json or {}).get("deposit_override"):
            raise HTTPException(400, conflict["message"])

        study.approved_by_user_id = user_id
        study.approved_at = datetime.now(timezone.utc)
        # The person this study was waiting for has now decided.
        evidence = dict(study.evidence_json or {})
        evidence.pop("awaiting_refetch_approval", None)
        # Recorded on BOTH routes, so a verdict can always say which kind of validation produced it
        # instead of inferring it from an absence.
        evidence["route"] = "deposit" if route == "deposit" else "pipeline"
        study.evidence_json = evidence
        old_state = study.state
        study.state = target
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
    async def override_deposit_conflict(
        session: AsyncSession, study_id: int, org_id: int, user_id: int, reason: str
    ) -> ValidationStudy:
        """ "The deposit is mislabelled, run it anyway": the second way out of the C1 refusal.

        The deposit is usually right, which is why refusing is the default. But a depositor can
        label a series wrong, and before this the scientist's only remaining control was Decline,
        which is terminal. So the way through exists and costs something: a stated reason, recorded
        with who gave it, kept on the study so a verdict that later diverges can be argued against
        the choice that produced it rather than being merely surprising.

        Re-pointing the plan is the other way out and it is the primary one. An override that is
        easier to click than the correction becomes the default action, and then the guard that
        caught study 14 running atacseq over Bisulfite-Seq means nothing.
        """
        study = await ValidationStudyService._load(session, study_id, org_id)
        if study.state != "plan_ready":
            raise HTTPException(
                400,
                f"Cannot override the deposit from '{study.state}'; the study must be in 'plan_ready'.",
            )
        if not (reason or "").strip():
            raise HTTPException(400, "Say why the deposit should be overruled; the reason goes on the record.")

        plan = await ReproductionPlanService.get_plan(session, study_id, org_id)
        conflict = deposit_conflict(plan.blockers_json if plan else None, plan.library_strategy if plan else None)
        if conflict is None:
            raise HTTPException(400, "This plan does not contradict what the deposit says its data is.")

        study.evidence_json = {
            **(study.evidence_json or {}),
            "deposit_override": {
                "user_id": user_id,
                "at": datetime.now(timezone.utc).isoformat(),
                "reason": reason.strip(),
                "pipeline_key": plan.pipeline_key if plan else None,
                "library_strategy": conflict["library_strategy"],
            },
        }
        await session.flush()
        await log_action(
            session,
            user_id=user_id,
            entity_type="validation_study",
            entity_id=study_id,
            action="deposit_conflict_overridden",
            details={
                "reason": reason.strip(),
                "pipeline_key": plan.pipeline_key if plan else None,
                "library_strategy": conflict["library_strategy"],
            },
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
