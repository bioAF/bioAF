"""C1 reproduction-plan approval gate (lit_validation).

A scientist ratifies the plan before any compute is spent: approve advances plan_ready ->
acquiring_data and stamps the approver; decline is a terminal plan_declined. Both are org-scoped and
audited.
"""

import pytest
from fastapi import HTTPException

from app.services.validation_study_service import ValidationStudyService

_TO_PLAN_READY = ["acquiring_text", "reading", "plan_ready"]


async def _study_at_plan_ready(session, admin_user):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    for nxt in _TO_PLAN_READY:
        study = await ValidationStudyService.transition(session, study.id, admin_user.organization_id, admin_user.id, nxt)
    return study


@pytest.mark.asyncio
async def test_approve_plan_advances_to_acquiring_data_and_stamps_approver(session, admin_user):
    study = await _study_at_plan_ready(session, admin_user)
    approved = await ValidationStudyService.approve_plan(
        session, study.id, admin_user.organization_id, admin_user.id
    )
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
        await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id + 999, admin_user.id
        )
    assert ei.value.status_code == 404
