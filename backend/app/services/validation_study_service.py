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
from app.services.validation_precompute_checks import species_hold
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


RENEWED_SELECTION_REQUIRED = (
    "This plan was read before bioAF separated a paper's experiments, and its contrasts span more than "
    "one assay. Choose the contrast this run checks at the gate before it resumes."
)


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
        intended_route: str | None = None,
    ) -> ValidationStudy:
        """Create a study in the initial ``requested`` state, with an audited create.

        ``intended_route`` is the route the requester chose at the button, before anything about the
        paper is known. When it is set the driver reads the paper and approves onto that route by
        itself; when it is None the manual C1 gate stands, which is what every other entry point
        still gets.
        """
        study = ValidationStudy(
            organization_id=org_id,
            requested_by_user_id=user_id,
            paper_id=paper_id,
            source_doi=source_doi,
            source_accession=source_accession,
            intended_route=intended_route,
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
    async def _spawn_sibling(session: AsyncSession, study: ValidationStudy, user_id: int) -> ValidationStudy:
        """A second study over the same paper, approved onto the RAW READS route.

        Its plan is copied, not re-extracted: the paper has already been read and a second LLM call
        would cost money to produce the same answer. Each study names the other, so neither reads as
        an orphan duplicate in the list.

        change_7.2 section 8: ONE sibling per authorization. Nothing checked, so a retried approval
        would create a second study over the same paper on the same route, with its own copied plan
        and its own compute bill, and the first would be orphaned by the forward pointer moving.
        """
        from app.models.reproduction_plan import ReproductionPlan

        existing_id = (study.evidence_json or {}).get("sibling_study_id")
        if existing_id:
            existing = (
                await session.execute(
                    select(ValidationStudy).where(
                        ValidationStudy.id == int(existing_id),
                        ValidationStudy.organization_id == study.organization_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing

        sibling = ValidationStudy(
            organization_id=study.organization_id,
            paper_id=study.paper_id,
            source_doi=study.source_doi,
            source_accession=study.source_accession,
            requested_by_user_id=study.requested_by_user_id,
            approved_by_user_id=user_id,
            approved_at=datetime.now(timezone.utc),
            state="acquiring_data",
            evidence_json={"route": "pipeline", "sibling_study_id": study.id},
        )
        session.add(sibling)
        await session.flush()

        plan = await ReproductionPlanService.get_plan(session, study.id, study.organization_id)
        if plan is not None:
            copy = ReproductionPlan(
                validation_study_id=sibling.id,
                accessions_json=plan.accessions_json,
                sample_sheet_json=plan.sample_sheet_json,
                pipeline_key=plan.pipeline_key,
                pipeline_version=plan.pipeline_version,
                parameters_json=plan.parameters_json,
                differential_design_json=plan.differential_design_json,
                finding_claim_json=plan.finding_claim_json,
                tools_json=plan.tools_json,
                code_availability_json=plan.code_availability_json,
                library_strategy=plan.library_strategy,
                reference_genome=plan.reference_genome,
                reference_build=plan.reference_build,
                mapping_confidence=plan.mapping_confidence,
                mapping_notes=plan.mapping_notes,
                blockers_json=plan.blockers_json,
                extractor_model=plan.extractor_model,
                extractor_provider=plan.extractor_provider,
            )
            session.add(copy)
            await session.flush()
            sibling.reproduction_plan_id = copy.id
            await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="validation_study",
            entity_id=sibling.id,
            action="create",
            details={"spawned_from": study.id, "route": "pipeline", "reason": "approved with route=both"},
        )
        return sibling

    @staticmethod
    async def _refuse_route(
        session: AsyncSession, study: ValidationStudy, user_id: int, route: str, decision
    ) -> ValidationStudy:
        """A route the policy will not authorize: assess what can still be assessed, then state it.

        The approver is stamped, because a person did decide, and the decision they made is on the
        record beside what it produced.
        """
        from app.services.validation_assessment import conclude_without_execution, record_refusal

        study.approved_by_user_id = user_id
        study.approved_at = datetime.now(timezone.utc)
        evidence = record_refusal(study, route, decision)
        evidence.pop("awaiting_refetch_approval", None)
        evidence["route"] = "pipeline" if route == "pipeline" else "deposit"
        study.evidence_json = dict(evidence)
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="validation_study",
            entity_id=study.id,
            action="route_refused",
            details={"route": route, "action": decision.action, "reason": decision.reason},
        )
        await conclude_without_execution(session, study, decision.reason)
        await session.refresh(study)
        return study

    @staticmethod
    async def route_decision_for(session: AsyncSession, study: ValidationStudy, route: str):
        """The shared route policy's answer for this study, for either approval entrance.

        change_7.2 section 1. The two contested judgments are read from what the extraction and the
        pre-compute checks already recorded, rather than re-derived: re-deriving them would put a
        network fetch in the way of every approval and let an outage decide the answer.
        """
        from app.services.validation_route_policy import decide_route

        plan = await ReproductionPlanService.get_plan(session, study.id, study.organization_id)
        evidence = study.evidence_json or {}
        return decide_route(
            route=route,
            capabilities=evidence.get("capabilities") or {},
            conflict=deposit_conflict(plan.blockers_json if plan else None, plan.library_strategy if plan else None),
            species_hold=species_hold(evidence.get("precompute_checks")),
            deposit_override=bool(evidence.get("deposit_override")),
            species_override=bool(evidence.get("species_override")),
        )

    @staticmethod
    async def approve_plan(
        session: AsyncSession, study_id: int, org_id: int, user_id: int, *, route: str = "deposit"
    ) -> ValidationStudy:
        """C1 gate: ratify the plan and start the chosen route, stamping the approver.

        ``deposit`` (the default) advances to ``acquiring_processed`` and starts from the
        pre-processed data the authors published. ``pipeline`` advances to ``acquiring_data`` and
        re-runs the paper from its raw reads, which costs hours and real compute.

        ``both`` approves THIS study on the deposit route and spawns a sibling on the pipeline route.
        A study carries one state and one classification, so both cannot be one study, and the two
        answer different questions anyway: the deposit route tests the authors' analysis, the raw
        route tests their processing.

        The route is recorded on the study's evidence rather than on the plan, because it is a
        property of THIS attempt: the same plan can be tried both ways.
        """
        study = await ValidationStudyService._load(session, study_id, org_id)
        target = "acquiring_data" if route == "pipeline" else "acquiring_processed"
        if not can_transition(study.state, target):
            raise HTTPException(
                400,
                f"Cannot approve a plan from '{study.state}'; the study must be in 'plan_ready'.",
            )
        if (study.evidence_json or {}).get("awaiting_renewed_selection"):
            raise HTTPException(400, RENEWED_SELECTION_REQUIRED)

        # change_7.2 section 1: ONE policy, shared with the driver's entrance, answering four
        # independent questions. Study 33 was approved by hand onto a route that could never be
        # taken because this gate validated a deposit conflict and a species mismatch and had no
        # route-feasibility equivalent.
        decision = await ValidationStudyService.route_decision_for(session, study, route)

        # A contested scientific judgment still refuses and still has its two deliberate ways past:
        # re-point the plan at the pipeline the deposit names, or record why the deposit is wrong.
        # Neither is implicit, and neither is what a missing adapter, a missing input or a missing
        # authorization needs.
        if decision.overridable:
            raise HTTPException(400, decision.reason)

        if not decision.authorizes_execution:
            # A refusal is not a hold. Moving the guard here without this would leave the study at
            # `plan_ready` with an HTTP 400, and a study approved with no `intended_route` is not
            # claimed by the driver either, so it would sit there for ever: the same failure with a
            # different cause. Both entrances authorize the public assessment and state an outcome.
            return await ValidationStudyService._refuse_route(session, study, user_id, route, decision)

        study.approved_by_user_id = user_id
        study.approved_at = datetime.now(timezone.utc)
        # The person this study was waiting for has now decided.
        evidence = dict(study.evidence_json or {})
        evidence.pop("awaiting_refetch_approval", None)
        # Recorded on BOTH routes, so a verdict can always say which kind of validation produced it
        # instead of inferring it from an absence.
        evidence["route"] = "pipeline" if route == "pipeline" else "deposit"

        # `both`: the sibling reuses this study's PLAN rather than re-reading the paper. Same paper,
        # same extraction, and a second LLM read would spend money to produce a plan we already have.
        if route == "both":
            sibling = await ValidationStudyService._spawn_sibling(session, study, user_id)
            evidence["sibling_study_id"] = sibling.id
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
    async def override_species_mismatch(
        session: AsyncSession, study_id: int, org_id: int, user_id: int, reason: str
    ) -> ValidationStudy:
        """ "The deposit's annotation is wrong, run it anyway": the way past the species hold.

        Mirrors the deposit override exactly, and for the same reason. The deposit is usually right,
        which is why refusing is the default, but a depositor can annotate a series wrong and the
        scientist's only other control would be Decline, which is terminal. So the way through exists
        and costs something: a stated reason, recorded with who gave it, kept on the study so a
        verdict that later diverges can be argued against the choice that produced it.
        """
        study = await ValidationStudyService._load(session, study_id, org_id)
        if study.state != "plan_ready":
            raise HTTPException(
                400,
                f"Cannot override the species check from '{study.state}'; the study must be in 'plan_ready'.",
            )
        if not (reason or "").strip():
            raise HTTPException(400, "Say why the species mismatch should be overruled; the reason goes on the record.")

        checks = (study.evidence_json or {}).get("precompute_checks")
        if species_hold(checks) is None:
            raise HTTPException(400, "This study's plan and its deposit do not disagree about the organism.")

        study.evidence_json = {
            **(study.evidence_json or {}),
            "species_override": {
                "by_user_id": user_id,
                "at": datetime.now(timezone.utc).isoformat(),
                "reason": reason.strip(),
                "detail": (checks or {}).get("species_matches", {}).get("detail"),
            },
        }
        await session.flush()
        await log_action(
            session,
            user_id=user_id,
            entity_type="validation_study",
            entity_id=study_id,
            action="species_mismatch_overridden",
            details={"reason": reason.strip()},
        )
        return study

    @staticmethod
    async def set_deposit_selection(
        session: AsyncSession,
        study_id: int,
        org_id: int,
        user_id: int,
        *,
        primary_matrix: str,
        matrix_files: list[str],
        metadata_file: str | None,
        reason: str,
    ) -> ValidationStudy:
        """plan_7 step 15: a person picks which deposited file to reproduce from.

        The assisted counterpart of the model's ``select_deposit``, and it takes the SAME guard: a
        filename the deposit does not hold would send the download at a 404, whoever named it. The
        choice is recorded as a person's so the report never credits a model for it.
        """
        study = await ValidationStudyService._load(session, study_id, org_id)
        if study.state != "acquiring_processed":
            raise HTTPException(
                400,
                f"Cannot choose a deposited file from '{study.state}'; the study must be waiting at "
                "'acquiring_processed'.",
            )

        evidence = dict(study.evidence_json or {})
        inventory = evidence.get("deposit_inventory") or {}
        known = {e.get("filename") for e in inventory.get("entries") or []}
        wanted = list(dict.fromkeys([primary_matrix, *(matrix_files or [])]))
        unknown = [name for name in wanted if name not in known]
        if unknown:
            raise HTTPException(
                400,
                f"This study's GEO deposit does not hold {', '.join(unknown)}. Choose from the files listed.",
            )
        if metadata_file and metadata_file not in known:
            raise HTTPException(400, f"This study's GEO deposit does not hold {metadata_file}.")

        evidence["deposit_selection"] = {
            "primary_matrix": primary_matrix,
            "matrix_files": wanted,
            "metadata_file": metadata_file,
            # Measured by step 6, which overrules any claim about it. Recording a guess here would
            # put a number on the record that the next step discards.
            "value_type": "unknown",
            "reason": (reason or "chosen at the approval gate").strip(),
            "confidence": 1.0,
            "declined": False,
            "decided_by": "human",
            "model": None,
            "chosen_by_user_id": user_id,
        }
        # A person answering the hold clears it: the study is no longer waiting on the thing the
        # hold recorded.
        evidence.pop("deposit_failed", None)
        study.evidence_json = evidence
        await session.flush()
        await log_action(
            session,
            user_id=user_id,
            entity_type="validation_study",
            entity_id=study_id,
            action="deposit_file_chosen",
            details={"primary_matrix": primary_matrix, "matrix_files": wanted},
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

    # change_7.2 section 3: the states a running acquisition can actually be stopped from. `/decline`
    # needs `plan_ready` and `/classify` needs `comparing`, so a study looping in `acquiring_processed`
    # could be stopped by NOTHING in the product. Studies 29 and 33 were parked in `error` by a direct
    # database write, which should never be the answer.
    # `samples_mismatch` is absent on purpose: it already has a product action (decline), and it is
    # a held decision rather than a running acquisition.
    CANCELLABLE_STATES = ("acquiring_data", "acquiring_processed", "inspecting_deposit")
    # Where a stopped or failed study can be picked up again. Both land at the C1 gate, because new
    # access or a corrected accession is a reason to DECIDE again, not to spend automatically.
    RESUMABLE_STATES = ("classified", "error")

    @staticmethod
    async def cancel_acquisition(
        session: AsyncSession, study_id: int, org_id: int, user_id: int, reason: str | None = None
    ) -> ValidationStudy:
        """Stop a running acquisition and state an outcome, without touching the database by hand.

        The claim is invalidated first. A cancellation that leaves the running claim valid can be
        overwritten by a late worker finishing its own step, which would silently undo it.
        """
        from app.services.validation_assessment import conclude_without_execution
        from app.services.validation_ownership import invalidate

        study = await ValidationStudyService._load(session, study_id, org_id)
        if study.state not in ValidationStudyService.CANCELLABLE_STATES:
            raise HTTPException(
                400,
                f"Cannot cancel acquisition from '{study.state}'; the study is not acquiring anything.",
            )

        await invalidate(session, study.id)
        stated = (reason or "").strip() or "stopped by a person"
        evidence = dict(study.evidence_json or {})
        evidence["cancelled"] = {
            "reason": stated,
            "at": datetime.now(timezone.utc).isoformat(),
            "by_user_id": user_id,
            "from_state": study.state,
        }
        evidence.pop("acquisition_retry_at", None)
        evidence.pop("awaiting_choice", None)
        study.evidence_json = evidence
        old_state = study.state
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="validation_study",
            entity_id=study.id,
            action="acquisition_cancelled",
            details={"reason": stated},
            previous_value={"state": old_state},
        )
        # Everything already established still counts. A cancellation ends the ATTEMPT, and it says
        # nothing about the paper, so the outcome carries the evidence and names the stop.
        await conclude_without_execution(
            session,
            study,
            stated,
            limitation={
                "kind": "failed_discovery",
                "resource": study.source_accession or "this paper's deposits",
                "operation": study.intended_route or "deposit",
                "detail": f"the acquisition was stopped before it completed ({stated})",
            },
        )
        await session.refresh(study)
        return study

    @staticmethod
    async def resume_study(
        session: AsyncSession, study_id: int, org_id: int, user_id: int, reason: str | None = None
    ) -> ValidationStudy:
        """Pick a stopped or failed study back up at the C1 gate.

        New credentials, a corrected accession or a newly public deposit are reasons to decide again,
        not to spend automatically, which is why this lands at `plan_ready` rather than back on a
        route. The previous assessment is not erased by the transition.
        """
        from app.services.validation_ownership import invalidate

        study = await ValidationStudyService._load(session, study_id, org_id)
        if study.state not in ValidationStudyService.RESUMABLE_STATES:
            raise HTTPException(
                400,
                f"Cannot resume from '{study.state}'; only a stopped or failed study can be resumed.",
            )

        await invalidate(session, study.id)
        evidence = dict(study.evidence_json or {})
        # The counters that would otherwise refuse the very attempt this resumption authorises.
        for key in ("acquisition_attempts", "acquisition_retry_at", "route_blocked", "discovery_unresolved"):
            evidence.pop(key, None)
        # change_7.4 section 1.1: a resumed study re-derives why its deposit could not serve. The hold
        # and its blocker are cleared, and so is a declined choice, so the next attempt decides again
        # and records the cause where it arises. A record written before causes held only wording.
        for key in ("deposit_failed", "deposit_unusable", "deposit_unusable_cause", "analysis_readiness"):
            evidence.pop(key, None)
        if (evidence.get("deposit_selection") or {}).get("declined"):
            evidence.pop("deposit_selection", None)
        # change_7.5 section 3.3: an unresolved mapping resumes into mapping with the same input, whose
        # revision is still current. It never re-enters acquisition.
        choice = evidence.get("input_choice")
        if isinstance(choice, dict) and (choice.get("mapping_validation") or {}).get("status") == "unresolved":
            evidence["input_choice"] = {**choice, "remap": True}
        # change_7.5 section 2.2: a plan read before experiments existed whose contrasts span two assays
        # resumes only after a person renews the selection at the gate; nothing acquires or launches
        # for it until then.
        from app.services.reported_experiments import legacy_needs_renewed_selection

        plan = await ReproductionPlanService.get_plan(session, study.id, org_id)
        if plan is not None and legacy_needs_renewed_selection(plan):
            evidence["awaiting_renewed_selection"] = {
                "reason": RENEWED_SELECTION_REQUIRED,
                "at": datetime.now(timezone.utc).isoformat(),
            }
        evidence["resumed"] = {
            "at": datetime.now(timezone.utc).isoformat(),
            "by_user_id": user_id,
            "reason": (reason or "").strip() or "resumed by a person",
            "from_state": study.state,
        }
        study.evidence_json = evidence
        # change_7.5 section 2.6: "Review and resume" invalidates by revision. Whatever was computed
        # for an earlier selection leaves for history and is never reused.
        if plan is not None:
            from app.services.validation_revisions import sync_revisions

            sync_revisions(study, plan)
        await session.flush()

        resumed = await ValidationStudyService.transition(session, study.id, org_id, user_id, "plan_ready")
        return resumed

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
