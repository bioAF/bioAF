"""ValidationStudy aggregate: persistence + audited, state-machine-guarded transitions (A1)."""

import pytest
from fastapi import HTTPException

from app.services.validation_study_service import ValidationStudyService

_HAPPY = ["acquiring_text", "reading", "plan_ready", "acquiring_data", "setup", "running", "extracting", "comparing"]


async def _walk(session, study, org_id, user_id, states):
    for nxt in states:
        study = await ValidationStudyService.transition(session, study.id, org_id, user_id, nxt)
    return study


@pytest.mark.asyncio
async def test_create_study_defaults_to_requested(session, admin_user):
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_doi="10.1234/x", source_accession="GSE1"
    )
    await session.commit()
    assert study.id is not None
    assert study.state == "requested"
    assert study.classification is None
    assert study.organization_id == admin_user.organization_id
    assert study.requested_by_user_id == admin_user.id
    assert study.source_doi == "10.1234/x"
    assert study.source_accession == "GSE1"


@pytest.mark.asyncio
async def test_transition_follows_the_happy_path(session, admin_user):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    study = await _walk(session, study, admin_user.organization_id, admin_user.id, _HAPPY)
    await session.commit()
    assert study.state == "comparing"


@pytest.mark.asyncio
async def test_invalid_transition_is_rejected_with_next_valid_listed(session, admin_user):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.commit()
    with pytest.raises(HTTPException) as ei:
        await ValidationStudyService.transition(session, study.id, admin_user.organization_id, admin_user.id, "running")
    assert ei.value.status_code == 400
    assert "Cannot transition from 'requested' to 'running'" in ei.value.detail
    assert "acquiring_text" in ei.value.detail


@pytest.mark.asyncio
async def test_reaching_classified_requires_a_valid_classification(session, admin_user):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    study = await _walk(session, study, admin_user.organization_id, admin_user.id, _HAPPY)

    with pytest.raises(HTTPException) as ei:  # missing classification
        await ValidationStudyService.transition(session, study.id, admin_user.organization_id, admin_user.id, "classified")
    assert ei.value.status_code == 400

    with pytest.raises(HTTPException):  # invalid classification
        await ValidationStudyService.transition(
            session, study.id, admin_user.organization_id, admin_user.id, "classified", classification="great"
        )

    study = await ValidationStudyService.transition(
        session, study.id, admin_user.organization_id, admin_user.id, "classified", classification="validated"
    )
    await session.commit()
    assert study.state == "classified"
    assert study.classification == "validated"


@pytest.mark.asyncio
async def test_early_exit_from_reading_to_classified(session, admin_user):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    study = await _walk(session, study, admin_user.organization_id, admin_user.id, ["acquiring_text", "reading"])
    study = await ValidationStudyService.transition(
        session, study.id, admin_user.organization_id, admin_user.id, "classified", classification="missing_data"
    )
    await session.commit()
    assert study.state == "classified"
    assert study.classification == "missing_data"


@pytest.mark.asyncio
async def test_terminal_state_rejects_further_transitions(session, admin_user):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    study = await _walk(session, study, admin_user.organization_id, admin_user.id, ["acquiring_text", "reading"])
    study = await ValidationStudyService.transition(
        session, study.id, admin_user.organization_id, admin_user.id, "classified", classification="missing_data"
    )
    with pytest.raises(HTTPException) as ei:
        await ValidationStudyService.transition(session, study.id, admin_user.organization_id, admin_user.id, "reading")
    assert ei.value.status_code == 400
    assert "none (terminal state)" in ei.value.detail


@pytest.mark.asyncio
async def test_transition_to_error_records_failure_reason(session, admin_user):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    study = await ValidationStudyService.transition(
        session, study.id, admin_user.organization_id, admin_user.id, "error", failure_reason="fetch timed out"
    )
    await session.commit()
    assert study.state == "error"
    assert study.failure_reason == "fetch timed out"


@pytest.mark.asyncio
async def test_study_persists_linked_pipeline_run_ids(session, admin_user):
    """A1 spine holds the fetchngs (data) and analysis pipeline-run links the driver sets."""
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    assert study.data_run_id is None
    assert study.analysis_run_id is None
    study.data_run_id = 4242
    study.analysis_run_id = 4343
    await session.commit()
    await session.refresh(study)
    assert study.data_run_id == 4242
    assert study.analysis_run_id == 4343


@pytest.mark.asyncio
async def test_transition_is_org_scoped(session, admin_user):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.commit()
    with pytest.raises(HTTPException) as ei:
        await ValidationStudyService.transition(
            session, study.id, admin_user.organization_id + 999, admin_user.id, "acquiring_text"
        )
    assert ei.value.status_code == 404
