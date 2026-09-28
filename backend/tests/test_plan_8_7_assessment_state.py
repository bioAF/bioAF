"""plan_8_7 stage 2: assessment progress is its own fact, not something inferred from `classified`.

    "Represent assessment progress independently of execution state on the existing study:
    discovering/reviewing evidence, current published revision, pending checks and execution status
    must not be inferred solely from `classified`."

`classified` is the end of the EXECUTION lifecycle. A study whose data could not be acquired and a
study whose evidence was reviewed in full both reach it, so a reader could not tell a report that was
still being built from one that was finished, and nothing could say which revision they were reading.

Two nullable columns, so every historical row reads as None and its report is projected from `state`
exactly as it was.
"""

import pytest

from app.services.validation_assessment import (
    ASSESSMENT_BLOCKED,
    ASSESSMENT_DISCOVERING,
    ASSESSMENT_PUBLISHED,
    ASSESSMENT_REVIEWING,
    assessment_progress,
)
from app.services.validation_study_service import ValidationStudyService


async def _study(session, admin_user, **kw):
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_accession="GSE1", intended_route="assessment"
    )
    for key, value in kw.items():
        setattr(study, key, value)
    await session.flush()
    return study


class TestItIsRecordedRatherThanInferred:
    @pytest.mark.asyncio
    async def test_a_new_study_is_discovering(self, session, admin_user):
        study = await _study(session, admin_user)
        assert assessment_progress(study)["state"] == ASSESSMENT_DISCOVERING

    @pytest.mark.asyncio
    async def test_a_recorded_state_is_what_is_reported(self, session, admin_user):
        study = await _study(session, admin_user, assessment_state=ASSESSMENT_REVIEWING, assessment_revision=2)
        found = assessment_progress(study)
        assert found["state"] == ASSESSMENT_REVIEWING
        assert found["revision"] == 2
        assert found["derived"] is False

    @pytest.mark.asyncio
    async def test_a_historical_row_is_projected_from_its_state_and_says_so(self, session, admin_user):
        study = await _study(session, admin_user, state="classified", assessment_state=None)
        found = assessment_progress(study)
        assert found["state"] == ASSESSMENT_PUBLISHED
        assert found["derived"] is True

    @pytest.mark.asyncio
    async def test_a_failed_read_is_blocked_not_published(self, session, admin_user):
        study = await _study(session, admin_user, state="error", assessment_state=None)
        assert assessment_progress(study)["state"] == ASSESSMENT_BLOCKED


class TestExecutionStatusTravelsBesideIt:
    @pytest.mark.asyncio
    async def test_an_assessment_only_study_reports_no_execution_and_no_blocker(self, session, admin_user):
        study = await _study(session, admin_user, assessment_state=ASSESSMENT_PUBLISHED)
        found = assessment_progress(study)
        assert found["execution"]["status"] == "not_requested"
        assert found["execution"]["available"] is True

    @pytest.mark.asyncio
    async def test_a_chosen_route_reports_its_execution_state(self, session, admin_user):
        study = await _study(session, admin_user, intended_route="deposit", state="running")
        assert assessment_progress(study)["execution"]["status"] == "running"

    @pytest.mark.asyncio
    async def test_a_blocked_route_does_not_make_the_assessment_blocked(self, session, admin_user):
        """ "A pending execution is one pending check, not a reason to withhold the rest of the report.\""""
        study = await _study(
            session,
            admin_user,
            intended_route="deposit",
            assessment_state=ASSESSMENT_PUBLISHED,
            evidence_json={"route_blocked": {"chosen": "deposit", "reason": "no processed data"}},
        )
        found = assessment_progress(study)
        assert found["state"] == ASSESSMENT_PUBLISHED
        assert found["execution"]["status"] == "blocked"
        assert found["execution"]["reason"] == "no processed data"


class TestTheStageRecordsIt:
    @pytest.mark.asyncio
    async def test_publishing_an_assessment_records_the_state_and_the_revision(self, session, admin_user):
        from app.services.validation_assessment import publish_assessment

        study = await _study(session, admin_user)
        study.evidence_json = {"paper_index": {"passages": []}}
        await session.flush()
        await publish_assessment(session, study, reason="a test")
        assert study.assessment_state == ASSESSMENT_PUBLISHED
        assert study.assessment_revision is not None
