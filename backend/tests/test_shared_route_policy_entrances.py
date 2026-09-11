"""change_7.2 section 1: both approval entrances reach the same decision from the same policy.

Study 32 was refused by the driver and reached a stated outcome. Study 33 was approved by hand
against a byte-identical capability block and walked onto a route that could never be taken, then
held for 47 minutes until someone stopped it with a database write. `approve_plan` validated a
deposit conflict and a species mismatch and had no route-feasibility equivalent.

Moving the guard is not sufficient on its own. An `HTTPException` from `approve_plan` leaves the
study at `plan_ready`, and a study approved from the gate with no `intended_route` is not claimed by
the driver either, so it would sit there indefinitely: the same failure with a different cause.
Both entrances must authorize the public assessment and drive the study to a stated outcome.
"""

import pytest
from fastapi import HTTPException

from app.services.validation_driver_service import ValidationDriverService
from app.services.validation_route_policy import CONTESTED, NO_ADAPTER, decide_route
from app.services.validation_study_service import ValidationStudyService

_TO_PLAN_READY = ["acquiring_text", "reading", "plan_ready"]

_EGA_CAPS = {
    "deposit_exists": {"value": "yes"},
    "raw_data": {"value": "yes"},
    "preprocessed_data": {"value": "no"},
    "deposits": [
        {
            "archive": "ega",
            "accession": "EGAS00001003667",
            "exists": "yes",
            "access": "controlled",
            "supported": "no",
            "raw_data": "yes",
            "preprocessed_data": "no",
        }
    ],
}

_RUNNABLE_CAPS = {
    "raw_data": {"value": "yes"},
    "preprocessed_data": {"value": "yes"},
    "deposits": [
        {
            "archive": "geo",
            "accession": "GSE309060",
            "exists": "yes",
            "access": "open",
            "supported": "yes",
            "raw_data": "yes",
            "preprocessed_data": "yes",
        }
    ],
}


async def _study(session, admin_user, *, capabilities=None, intended_route=None):
    study = await ValidationStudyService.create_study(
        session,
        admin_user.organization_id,
        admin_user.id,
        source_accession="EGAS00001003667",
        intended_route=intended_route,
    )
    for nxt in _TO_PLAN_READY:
        study = await ValidationStudyService.transition(
            session, study.id, admin_user.organization_id, admin_user.id, nxt
        )
    if capabilities is not None:
        study.evidence_json = {**(study.evidence_json or {}), "capabilities": capabilities}
        await session.flush()
    return study


class TestHandApprovalValidatesItsRoute:
    @pytest.mark.asyncio
    async def test_an_unacquirable_route_is_not_approved_onto_acquisition(self, session, admin_user):
        """Study 33's actual failure: an EGA deposit with no adapter reached `acquiring_processed`
        and listed the same empty accession every 30 seconds until it was stopped by hand."""
        study = await _study(session, admin_user, capabilities=_EGA_CAPS)
        approved = await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id, admin_user.id, route="pipeline"
        )
        assert approved.state != "acquiring_data"
        assert approved.state != "acquiring_processed"

    @pytest.mark.asyncio
    async def test_it_reaches_a_stated_outcome_instead_of_holding(self, session, admin_user):
        study = await _study(session, admin_user, capabilities=_EGA_CAPS)
        approved = await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id, admin_user.id, route="pipeline"
        )
        assert approved.state == "classified"
        assert approved.classification is not None
        assert approved.failure_reason

    @pytest.mark.asyncio
    async def test_the_recorded_reason_names_the_axis_that_refused(self, session, admin_user):
        study = await _study(session, admin_user, capabilities=_EGA_CAPS)
        approved = await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id, admin_user.id, route="pipeline"
        )
        assert (approved.evidence_json or {}).get("route_decision", {}).get("action") == NO_ADAPTER

    @pytest.mark.asyncio
    async def test_approval_with_no_intended_route_still_reaches_an_outcome(self, session, admin_user):
        """`_driver_owns` only claims a `plan_ready` study when `intended_route` is set, so a study
        refused at the gate with none would never be picked up again."""
        study = await _study(session, admin_user, capabilities=_EGA_CAPS, intended_route=None)
        approved = await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id, admin_user.id, route="deposit"
        )
        assert approved.intended_route is None
        assert approved.state == "classified"

    @pytest.mark.asyncio
    async def test_a_runnable_route_is_still_approved(self, session, admin_user):
        study = await _study(session, admin_user, capabilities=_RUNNABLE_CAPS)
        approved = await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id, admin_user.id, route="deposit"
        )
        assert approved.state == "acquiring_processed"
        assert approved.approved_by_user_id == admin_user.id

    @pytest.mark.asyncio
    async def test_an_undetermined_route_is_still_approved(self, session, admin_user):
        """A discovery failure is not an absence, and refusing here would hide a workable route
        behind a timeout."""
        study = await _study(
            session, admin_user, capabilities={"preprocessed_data": {"value": "unknown"}, "deposits": []}
        )
        approved = await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id, admin_user.id, route="deposit"
        )
        assert approved.state == "acquiring_processed"

    @pytest.mark.asyncio
    async def test_a_contested_judgment_still_refuses_with_an_override_available(self, session, admin_user):
        """The two existing overridable refusals are unchanged: a person answers them."""
        decision = decide_route(
            route="deposit",
            capabilities=_RUNNABLE_CAPS,
            conflict={"message": "the deposit declares Bisulfite-Seq"},
        )
        assert decision.action == CONTESTED
        assert decision.overridable is True


class TestTheTwoEntrancesAgree:
    @pytest.mark.asyncio
    async def test_the_driver_reaches_the_same_action_as_the_gate(self, session, admin_user):
        study = await _study(session, admin_user, capabilities=_EGA_CAPS, intended_route="pipeline")
        await ValidationDriverService._handle_plan_ready(session, study)
        await session.flush()
        driver_action = (study.evidence_json or {}).get("route_decision", {}).get("action")

        other = await _study(session, admin_user, capabilities=_EGA_CAPS)
        approved = await ValidationStudyService.approve_plan(
            session, other.id, admin_user.organization_id, admin_user.id, route="pipeline"
        )
        assert driver_action == (approved.evidence_json or {}).get("route_decision", {}).get("action")

    @pytest.mark.asyncio
    async def test_both_reach_a_terminal_state_on_a_refused_route(self, session, admin_user):
        study = await _study(session, admin_user, capabilities=_EGA_CAPS, intended_route="deposit")
        await ValidationDriverService._handle_plan_ready(session, study)
        await session.flush()
        assert study.state == "classified"

        other = await _study(session, admin_user, capabilities=_EGA_CAPS)
        approved = await ValidationStudyService.approve_plan(
            session, other.id, admin_user.organization_id, admin_user.id, route="deposit"
        )
        assert approved.state == "classified"

    @pytest.mark.asyncio
    async def test_the_deposit_leg_of_an_unreadable_archive_refuses_on_the_adapter(self, session, admin_user):
        """change_7.3 section 5 (flagged test change): this asserted NO_INPUT for the EGA deposit leg,
        the axis-order defect the plan names. `no_input` means the adapter exists and the resource
        holds nothing; for EGA the adapter does not exist, and the listing is an observation."""
        study = await _study(session, admin_user, capabilities=_EGA_CAPS)
        approved = await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id, admin_user.id, route="deposit"
        )
        assert (approved.evidence_json or {}).get("route_decision", {}).get("action") == NO_ADAPTER


class TestTheGateStillRefusesWhatItAlwaysDid:
    @pytest.mark.asyncio
    async def test_approving_from_the_wrong_state_is_still_a_400(self, session, admin_user):
        study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
        with pytest.raises(HTTPException) as ei:
            await ValidationStudyService.approve_plan(
                session, study.id, admin_user.organization_id, admin_user.id, route="pipeline"
            )
        assert ei.value.status_code == 400
