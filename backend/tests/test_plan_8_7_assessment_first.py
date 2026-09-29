"""plan_8_7 stage 2: clicking Validate starts the assessment, not a cost decision.

    "Make the default Validate action start the existing assessment work without requiring a
    raw/processed route decision. Keep advanced execution preferences and their spend authorization
    explicit. Preserve legacy callers with explicit route choices."

The route chooser stood in front of everything: a reader who wanted to know what a paper's evidence
says had to first authorize either a deposit reproduction or hours of cluster compute. Discovery is
what establishes which route is even possible, so the question was being asked before its answer
existed.

`assessment` is the default now. It self-drives the read, discovery and assessment exactly as a
chosen route does, and it stops before the execution gate rather than spending anything. An explicit
`deposit`, `pipeline` or `both` is unchanged, and so is a caller that sends no route at all.
"""

import pytest

from app.services.validation_route_policy import ASSESSMENT
from app.services.validation_study_service import ValidationStudyService


async def _study(session, admin_user, route):
    return await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_accession="GSE1", intended_route=route
    )


class TestTheDefaultRouteAuthorizesNoExecution:
    def test_assessment_is_a_route_the_policy_knows(self):
        from app.services.validation_route_policy import ROUTE_NEEDS, ROUTE_REQUIREMENTS, decide_route

        assert ASSESSMENT not in ROUTE_REQUIREMENTS, "it requires nothing of the paper"
        assert ASSESSMENT not in ROUTE_NEEDS
        assert decide_route(route=ASSESSMENT, capabilities={}).authorizes_execution is False

    def test_it_is_refused_no_capability_and_blames_nothing(self):
        from app.services.validation_route_policy import decide_route

        decision = decide_route(route=ASSESSMENT, capabilities={"preprocessed_data": {"value": "no"}})
        assert decision.authorizes_execution is False
        assert decision.action not in ("no_adapter", "no_input", "not_authorized")

    @pytest.mark.asyncio
    async def test_the_driver_owns_a_study_requested_for_assessment(self, session, admin_user):
        from app.services.validation_driver_service import _driver_owns

        study = await _study(session, admin_user, ASSESSMENT)
        assert _driver_owns(study) is True

    @pytest.mark.asyncio
    async def test_a_study_with_no_route_at_all_still_waits_for_a_person(self, session, admin_user):
        from app.services.validation_driver_service import _driver_owns

        study = await _study(session, admin_user, None)
        assert _driver_owns(study) is False


class TestItStopsBeforeSpendingAnything:
    @pytest.mark.asyncio
    async def test_a_ready_plan_is_assessed_and_not_approved(self, session, admin_user, monkeypatch):
        from app.services.validation_driver_service import ValidationDriverService

        study = await _study(session, admin_user, ASSESSMENT)
        study.state = "plan_ready"
        await session.flush()
        approved = []
        monkeypatch.setattr(ValidationStudyService, "approve_plan", classmethod(lambda *a, **k: approved.append(k)))
        assessed = []

        async def _conclude(session, study, reason, **kw):
            assessed.append(reason)

        monkeypatch.setattr(ValidationDriverService, "_finish_without_execution", staticmethod(_conclude))
        moved = await ValidationDriverService._handle_plan_ready(session, study)
        assert moved is True
        assert approved == []
        assert assessed and "assessment" in assessed[0].lower()

    @pytest.mark.asyncio
    async def test_an_explicit_route_is_still_approved(self, session, admin_user, monkeypatch):
        from app.services.validation_driver_service import ValidationDriverService

        study = await _study(session, admin_user, "deposit")
        study.state = "plan_ready"
        study.evidence_json = {"capabilities": {"preprocessed_data": {"value": "yes"}}}
        await session.flush()
        approved = []

        async def _approve(cls, session, study_id, org_id, user_id, *, route=None, **kw):
            approved.append(route)

        monkeypatch.setattr(ValidationStudyService, "approve_plan", classmethod(_approve))
        await ValidationDriverService._handle_plan_ready(session, study)
        assert approved == ["deposit"]


class TestTheReportSaysExecutionIsAvailableNotImpossible:
    @pytest.mark.asyncio
    async def test_the_completion_names_the_choice_rather_than_a_blocker(self, session, admin_user):
        from app.services.validation_assessment import assessment_only_reason

        assert "advanced" in assessment_only_reason().lower() or "choose" in assessment_only_reason().lower()
        assert "cannot" not in assessment_only_reason().lower()
