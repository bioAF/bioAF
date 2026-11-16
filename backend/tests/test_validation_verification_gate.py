"""01C.2: a validation study consumes only fetched FASTQs whose checksum verification allows it.

The required inputs are the current acquisition's FASTQs of the samples the approved design picks
(every current-acquisition FASTQ for a QC-only study). Pending verification keeps the study in
``acquiring_data`` with its design untouched; a checksum mismatch or a terminal verification failure
puts it in ``error`` with a safe reason and no scientific classification; an unrelated or unselected
file never decides the study. Retry and setup use the same eligibility check.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.models.file import File
from app.models.file_verification import FileVerification
from app.models.sample import Sample
from app.services.fetchngs_ingest_service import FetchngsIngestService
from app.services.pipeline_run_service import PipelineRunService
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_driver_service import ValidationDriverService
from app.services.validation_study_service import ValidationStudyService
from tests.test_validation_driver_backhalf import _experiment_id, _LaunchSpy, _run, _study

pytestmark = pytest.mark.asyncio

DESIGN = {"contrasts": [{"test_samples": ["SRR1"], "reference_samples": ["SRR2"], "subjects": {}}]}


async def _fetched_sample(session, user, exp_id, run_id, accession, *, status, ready=True):
    sample = Sample(
        experiment_id=exp_id,
        external_id=f"SRX-{accession}",
        status="registered",
        prep_notes=f"run_accession={accession}",
    )
    session.add(sample)
    await session.flush()
    f = File(
        organization_id=user.organization_id,
        storage_uri=f"s3://r/runs/{run_id}/fastq/{accession}_1.fastq.gz",
        filename=f"{accession}_1.fastq.gz",
        file_type="fastq",
        source_type="pipeline_output",
        source_pipeline_run_id=run_id,
        experiment_id=exp_id,
        tags_json=["read:R1"],
    )
    if ready:
        from datetime import datetime, timezone

        f.ready_at = datetime.now(timezone.utc)
    session.add(f)
    await session.flush()
    await session.execute(
        text("INSERT INTO sample_files (sample_id, file_id) VALUES (:s, :f)"), {"s": sample.id, "f": f.id}
    )
    if status is not None:
        session.add(
            FileVerification(
                organization_id=user.organization_id,
                file_id=f.id,
                status=status,
                expected_algorithm="md5",
                expected_checksum="a" * 32,
                observed_checksum="b" * 32 if status == "checksum_mismatch" else None,
                error_code="checksum_mismatch"
                if status == "checksum_mismatch"
                else ("storage_unavailable" if status == "failed" else None),
            )
        )
        await session.flush()
    return sample, f


async def _acquiring(session, user, monkeypatch, *, design=DESIGN, inputs):
    async def _noop(*a, **k):
        return []

    monkeypatch.setattr(FetchngsIngestService, "ingest_for_run", _noop)
    monkeypatch.setattr(FetchngsIngestService, "attach_fastq_files", _noop)
    spy = _LaunchSpy()
    monkeypatch.setattr(PipelineRunService, "launch_run", spy)
    exp_id = await _experiment_id(session, user)
    study = await _study(session, user, state="acquiring_data", experiment_id=exp_id)
    fetch = await _run(session, user, exp_id, name="nf-core/fetchngs", status="completed")
    study.data_run_id = fetch.id
    plan = await ReproductionPlanService.get_plan(session, study.id, user.organization_id)
    plan.differential_design_json = design
    files = {}
    for accession, status, *rest in inputs:
        run_id = rest[0] if rest else fetch.id
        _, f = await _fetched_sample(
            session, user, exp_id, run_id, accession, status=status, ready=status in (None, "verified", "not_required")
        )
        files[accession] = f
    await session.flush()
    return study, files, spy


async def test_verified_selected_inputs_proceed_despite_an_unselected_failure(session, admin_user, monkeypatch):
    study, _, spy = await _acquiring(
        session,
        admin_user,
        monkeypatch,
        inputs=[("SRR1", "verified"), ("SRR2", "not_required"), ("SRR3", "checksum_mismatch")],
    )
    await ValidationDriverService.advance_active_studies(session)
    await session.refresh(study)
    assert study.state == "setup"
    assert spy.calls == []


async def test_pending_selected_input_waits_without_touching_the_design(session, admin_user, monkeypatch):
    study, _, spy = await _acquiring(
        session, admin_user, monkeypatch, inputs=[("SRR1", "verified"), ("SRR2", "verifying")]
    )
    for _ in range(3):
        assert await ValidationDriverService._advance_one(session, study) is False
    await session.refresh(study)
    assert study.state == "acquiring_data" and study.classification is None
    plan = await ReproductionPlanService.get_plan(session, study.id, admin_user.organization_id)
    assert plan.differential_design_json == DESIGN  # picks preserved, not rewritten or dropped
    assert spy.calls == []  # no refetch


@pytest.mark.parametrize("status", ["checksum_mismatch", "failed"])
async def test_a_mismatched_or_failed_selected_input_is_an_error_not_missing_data(
    session, admin_user, monkeypatch, status
):
    study, files, spy = await _acquiring(
        session, admin_user, monkeypatch, inputs=[("SRR1", "verified"), ("SRR2", status)]
    )
    await ValidationDriverService.advance_active_studies(session)
    await session.refresh(study)
    assert study.state == "error"
    assert study.classification is None
    assert "checksum" in (study.failure_reason or "").lower() or "verif" in (study.failure_reason or "").lower()
    assert str(files["SRR2"].id) in study.failure_reason
    assert spy.calls == []


async def test_qc_only_scope_is_the_current_acquisition(session, admin_user, monkeypatch):
    exp_scope_run = None
    study, files, _ = await _acquiring(
        session,
        admin_user,
        monkeypatch,
        design={},
        inputs=[("SRR1", "verified")],
    )
    # A FASTQ from an earlier acquisition of the same experiment, failed: not this study's input.
    old = await _run(session, admin_user, study.experiment_id, name="nf-core/fetchngs", status="completed")
    await _fetched_sample(
        session, admin_user, study.experiment_id, old.id, "SRR9", status="checksum_mismatch", ready=False
    )
    await session.flush()
    await ValidationDriverService.advance_active_studies(session)
    await session.refresh(study)
    assert study.state == "setup"
    assert exp_scope_run is None


async def test_retry_resumes_at_setup_only_with_usable_inputs(session, admin_user, monkeypatch):
    study, files, _ = await _acquiring(
        session, admin_user, monkeypatch, inputs=[("SRR1", "verified"), ("SRR2", "checksum_mismatch")]
    )
    await ValidationDriverService.advance_active_studies(session)
    await session.refresh(study)
    assert study.state == "error"

    retried = await ValidationStudyService.retry_study(session, study.id, admin_user.organization_id, admin_user.id)
    assert retried.state == "plan_ready"  # not setup: a raw File row is not a usable input


async def test_retry_resumes_at_setup_after_the_inputs_are_verified(session, admin_user, monkeypatch):
    from datetime import datetime, timezone

    study, files, _ = await _acquiring(
        session, admin_user, monkeypatch, inputs=[("SRR1", "verified"), ("SRR2", "checksum_mismatch")]
    )
    await ValidationDriverService.advance_active_studies(session)
    await session.refresh(study)
    record = (
        await session.execute(text("SELECT id FROM file_verifications WHERE file_id = :f"), {"f": files["SRR2"].id})
    ).scalar_one()
    await session.execute(
        text("UPDATE file_verifications SET status = 'verified', observed_checksum = expected_checksum WHERE id = :i"),
        {"i": record},
    )
    files["SRR2"].ready_at = datetime.now(timezone.utc)
    await session.flush()

    retried = await ValidationStudyService.retry_study(session, study.id, admin_user.organization_id, admin_user.id)
    assert retried.state == "setup"


async def test_setup_refuses_to_launch_on_inputs_that_became_unusable(session, admin_user, monkeypatch):
    study, files, spy = await _acquiring(
        session, admin_user, monkeypatch, inputs=[("SRR1", "verified"), ("SRR2", "verified")]
    )
    await ValidationDriverService.advance_active_studies(session)
    await session.refresh(study)
    assert study.state == "setup"
    await session.execute(
        text("UPDATE file_verifications SET status = 'checksum_mismatch' WHERE file_id = :f"), {"f": files["SRR2"].id}
    )
    await session.flush()
    await ValidationDriverService.advance_active_studies(session)
    await session.refresh(study)
    assert study.state == "error" and spy.calls == []


async def test_fastqs_fetched_before_verification_existed_stay_usable(session, admin_user, monkeypatch):
    """No verification record means the file predates the contract: unproven, but not invalidated."""
    study, files, _ = await _acquiring(session, admin_user, monkeypatch, inputs=[("SRR1", None), ("SRR2", None)])
    for f in files.values():
        f.ready_at = None
    await session.flush()
    await ValidationDriverService.advance_active_studies(session)
    await session.refresh(study)
    assert study.state == "setup"
