"""C1 reproduction-plan approval gate (lit_validation).

A scientist ratifies the plan before any compute is spent: approve advances plan_ready ->
acquiring_data and stamps the approver; decline is a terminal plan_declined. Both are org-scoped and
audited.
"""

import pytest
from fastapi import HTTPException

from app.services.pipeline_mapper import library_strategy_conflict
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_study_service import ValidationStudyService

_TO_PLAN_READY = ["acquiring_text", "reading", "plan_ready"]


async def _study_at_plan_ready(session, admin_user):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    for nxt in _TO_PLAN_READY:
        study = await ValidationStudyService.transition(
            session, study.id, admin_user.organization_id, admin_user.id, nxt
        )
    return study


@pytest.mark.asyncio
async def test_approve_plan_advances_to_acquiring_data_and_stamps_approver(session, admin_user):
    study = await _study_at_plan_ready(session, admin_user)
    approved = await ValidationStudyService.approve_plan(session, study.id, admin_user.organization_id, admin_user.id)
    await session.commit()
    assert approved.state == "acquiring_data"
    assert approved.approved_by_user_id == admin_user.id
    assert approved.approved_at is not None


@pytest.mark.asyncio
async def test_approve_plan_rejected_when_not_in_plan_ready(session, admin_user):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.commit()  # still in 'requested'
    with pytest.raises(HTTPException) as ei:
        await ValidationStudyService.approve_plan(session, study.id, admin_user.organization_id, admin_user.id)
    assert ei.value.status_code == 400
    assert "plan_ready" in ei.value.detail


@pytest.mark.asyncio
async def test_decline_plan_is_terminal_and_records_reason(session, admin_user):
    study = await _study_at_plan_ready(session, admin_user)
    declined = await ValidationStudyService.decline_plan(
        session, study.id, admin_user.organization_id, admin_user.id, reason="wrong accession"
    )
    await session.commit()
    assert declined.state == "plan_declined"
    assert declined.failure_reason == "wrong accession"
    # Terminal: no further transitions.
    with pytest.raises(HTTPException):
        await ValidationStudyService.transition(
            session, study.id, admin_user.organization_id, admin_user.id, "acquiring_data"
        )


@pytest.mark.asyncio
async def test_decline_plan_rejected_when_not_in_plan_ready(session, admin_user):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.commit()
    with pytest.raises(HTTPException) as ei:
        await ValidationStudyService.decline_plan(session, study.id, admin_user.organization_id, admin_user.id)
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_approve_plan_is_org_scoped(session, admin_user):
    study = await _study_at_plan_ready(session, admin_user)
    await session.commit()
    with pytest.raises(HTTPException) as ei:
        await ValidationStudyService.approve_plan(session, study.id, admin_user.organization_id + 999, admin_user.id)
    assert ei.value.status_code == 404


# --- retrying an errored study (the fetch is already paid for) -----------------------------------


async def _errored_study(session, admin_user, *, reason="analysis run failed"):
    study = await _study_at_plan_ready(session, admin_user)
    study = await ValidationStudyService.transition(
        session, study.id, admin_user.organization_id, admin_user.id, "error", failure_reason=reason
    )
    return study


@pytest.mark.asyncio
async def test_retry_resumes_at_setup_when_the_data_was_already_fetched(session, admin_user):
    """The failure this exists for: demo run 42 aligned against an iGenomes index that did not match
    its own fasta, after a 122 GB fetch had completed. Re-fetching to fix a launch parameter is pure
    waste, so a study that still has its fetched samples resumes at `setup`, which relaunches the
    analysis against them."""
    from app.models.experiment import Experiment
    from app.models.file import File
    from app.models.sample import Sample, sample_files

    study = await _errored_study(session, admin_user)
    exp = Experiment(
        name="Reproduction: retry", organization_id=admin_user.organization_id, owner_user_id=admin_user.id
    )
    session.add(exp)
    await session.flush()
    sample = Sample(experiment_id=exp.id, external_id="SRX1", organism="Homo sapiens")
    session.add(sample)
    await session.flush()
    f = File(
        organization_id=admin_user.organization_id,
        experiment_id=exp.id,
        gcs_uri="gs://b/SRX1_1.fastq.gz",
        storage_uri="gs://b/SRX1_1.fastq.gz",
        filename="SRX1_1.fastq.gz",
        file_type="fastq",
    )
    session.add(f)
    await session.flush()
    await session.execute(sample_files.insert().values(sample_id=sample.id, file_id=f.id))
    study.experiment_id = exp.id
    study.analysis_run_id = 999
    study.evidence_json = {"level3_run_session_id": 7, "qc": {"stale": True}}
    await session.flush()

    retried = await ValidationStudyService.retry_study(session, study.id, admin_user.organization_id, admin_user.id)
    await session.commit()

    assert retried.state == "setup"
    assert retried.failure_reason is None
    # The previous attempt's run and its Level-3 session must not be inherited, or the driver reads
    # the FAILED run as this attempt's result and reproduces from a session that no longer applies.
    assert retried.analysis_run_id is None
    assert "level3_run_session_id" not in (retried.evidence_json or {})


@pytest.mark.asyncio
async def test_retry_returns_to_the_gate_when_there_is_no_fetched_data(session, admin_user):
    """A study that failed before or during the fetch has nothing to resume from, and re-fetching
    spends real money. It goes back to the C1 gate so a human approves that spend deliberately
    rather than a retry button quietly starting a 122 GB download."""
    study = await _errored_study(session, admin_user, reason="data acquisition run failed to launch")

    retried = await ValidationStudyService.retry_study(session, study.id, admin_user.organization_id, admin_user.id)
    await session.commit()

    assert retried.state == "plan_ready"
    assert retried.failure_reason is None


@pytest.mark.asyncio
async def test_retry_is_rejected_for_a_study_that_did_not_error(session, admin_user):
    """Retry is for infra failures. A study that reached a verdict has a verdict, and a running one
    is still running; re-entering `setup` from either would launch a duplicate analysis."""
    study = await _study_at_plan_ready(session, admin_user)
    await session.commit()

    with pytest.raises(HTTPException) as ei:
        await ValidationStudyService.retry_study(session, study.id, admin_user.organization_id, admin_user.id)
    assert ei.value.status_code == 400
    assert "error" in ei.value.detail


@pytest.mark.asyncio
async def test_retry_is_org_scoped(session, admin_user):
    study = await _errored_study(session, admin_user)
    await session.commit()
    with pytest.raises(HTTPException) as ei:
        await ValidationStudyService.retry_study(session, study.id, admin_user.organization_id + 999, admin_user.id)
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_the_driver_never_retries_a_study_by_itself(session, admin_user):
    """The reason `error` stays terminal. If the background tick picked errored studies up, a launch
    parameter that is wrong for a given paper would relaunch on every tick forever, and each launch
    spends compute. Retry has to come from a person."""
    from app.services.validation_driver_service import _ACTIVE_BACK_HALF_STATES

    assert "error" not in _ACTIVE_BACK_HALF_STATES


# ---- a plan the deposited data contradicts cannot be approved (plan_4 step 2) ----
#
# Approving is what spends real money. Study 14 was planned as nf-core/atacseq over Bisulfite-Seq
# data at `mapping_confidence: exact`, and nothing between the plan and the fetch objected.


async def _study_with_plan(session, admin_user, *, pipeline_key, blockers):
    study = await _study_at_plan_ready(session, admin_user)
    await ReproductionPlanService.create_plan(
        session,
        study,
        admin_user.id,
        accessions=["SRP0001"],
        pipeline_key=pipeline_key,
        pipeline_version="1.0.0",
        blockers=blockers,
    )
    await session.flush()
    return study


@pytest.mark.asyncio
async def test_approving_a_plan_the_deposit_contradicts_is_refused(session, admin_user):
    conflict = library_strategy_conflict("nf-core/atacseq", "Bisulfite-Seq")
    study = await _study_with_plan(session, admin_user, pipeline_key="nf-core/atacseq", blockers=[conflict])

    with pytest.raises(HTTPException) as ei:
        await ValidationStudyService.approve_plan(session, study.id, admin_user.organization_id, admin_user.id)

    assert ei.value.status_code == 400
    # Names both sides, so the refusal is actionable rather than merely a stop.
    assert "nf-core/atacseq" in ei.value.detail
    assert "Bisulfite-Seq" in ei.value.detail


@pytest.mark.asyncio
async def test_a_refused_approval_leaves_the_study_at_the_gate(session, admin_user):
    conflict = library_strategy_conflict("nf-core/atacseq", "Bisulfite-Seq")
    study = await _study_with_plan(session, admin_user, pipeline_key="nf-core/atacseq", blockers=[conflict])

    with pytest.raises(HTTPException):
        await ValidationStudyService.approve_plan(session, study.id, admin_user.organization_id, admin_user.id)

    assert study.state == "plan_ready"
    assert study.approved_by_user_id is None


@pytest.mark.asyncio
async def test_a_contradicted_study_can_still_be_declined(session, admin_user):
    """The guard must not be a dead end. Declining is the human's way out, and it stays open."""
    conflict = library_strategy_conflict("nf-core/atacseq", "Bisulfite-Seq")
    study = await _study_with_plan(session, admin_user, pipeline_key="nf-core/atacseq", blockers=[conflict])

    declined = await ValidationStudyService.decline_plan(
        session, study.id, admin_user.organization_id, admin_user.id, reason="wrong pipeline for the data"
    )
    assert declined.state == "plan_declined"


@pytest.mark.asyncio
async def test_an_ordinary_blocker_never_blocks_approval(session, admin_user):
    """Plans carry blockers routinely (a second accession the paper names, a genome that did not
    resolve) and those are advisory. Only the deposit contradiction refuses."""
    study = await _study_with_plan(
        session,
        admin_user,
        pipeline_key="nf-core/rnaseq",
        blockers=["could not map the paper's reference genome 'hg18' to a known assembly"],
    )

    approved = await ValidationStudyService.approve_plan(
        session, study.id, admin_user.organization_id, admin_user.id
    )
    assert approved.state == "acquiring_data"


@pytest.mark.asyncio
async def test_a_study_with_no_plan_at_all_still_approves(session, admin_user):
    study = await _study_at_plan_ready(session, admin_user)
    approved = await ValidationStudyService.approve_plan(
        session, study.id, admin_user.organization_id, admin_user.id
    )
    assert approved.state == "acquiring_data"
