"""What bioAF is doing on a study right now, so the page can show it rather than offer a click.

Study 37 (2026-09-11): the requester chose a route at the button, the driver claimed the study four
seconds later and began reading it, and the page, loaded once in `requested` and never refreshed,
still offered "Read paper". The click lost the claim and came back as "Another worker is already
reading this paper", naming a job the requester could not see anywhere.

Two facts the page did not have: whether bioAF moves this study on by itself (then there is nothing
to click, and the page should keep itself current), and whether a worker holds it right now (then
the page can say the work is under way, and since when).
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.services import validation_ownership as own
from app.services.validation_driver_service import is_advancing, study_activity
from app.services.validation_study_service import ValidationStudyService


def _s(state, *, route="deposit", evidence=None):
    return SimpleNamespace(state=state, intended_route=route, evidence_json=evidence or {})


class TestWhetherBioafMovesTheStudyOn:
    def test_a_study_with_a_chosen_route_reads_itself(self):
        assert is_advancing(_s("requested")) is True

    def test_a_study_without_a_route_waits_for_a_read(self):
        assert is_advancing(_s("requested", route=None)) is False

    def test_a_chosen_route_is_approved_by_bioaf(self):
        assert is_advancing(_s("plan_ready")) is True

    def test_a_plan_without_a_route_waits_at_the_gate(self):
        assert is_advancing(_s("plan_ready", route=None)) is False

    def test_a_route_held_for_a_person_is_not_advancing(self):
        assert is_advancing(_s("plan_ready", evidence={"route_blocked": {"reason": "x"}})) is False

    @pytest.mark.parametrize(
        "state",
        [
            "acquiring_data",
            "acquiring_processed",
            "inspecting_deposit",
            "setup",
            "running",
            "extracting",
            "reproducing",
        ],
    )
    def test_every_back_half_state_is_advancing_with_or_without_a_route(self, state):
        assert is_advancing(_s(state)) is True
        assert is_advancing(_s(state, route=None)) is True

    @pytest.mark.parametrize("hold", ["awaiting_choice", "awaiting_adoption"])
    def test_a_hold_a_person_resolves_is_not_advancing(self, hold):
        assert is_advancing(_s("acquiring_processed", evidence={hold: {"reason": "x"}})) is False

    def test_comparing_advances_until_the_classifier_has_run(self):
        assert is_advancing(_s("comparing")) is True
        assert is_advancing(_s("comparing", evidence={"classification_result": {}})) is False

    @pytest.mark.parametrize("state", ["classified", "error", "plan_declined", "samples_mismatch"])
    def test_a_stopped_study_is_not_advancing(self, state):
        assert is_advancing(_s(state)) is False


async def _study(session, admin_user, *, route="deposit"):
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_doi="10.1126/sciadv.abf2229", intended_route=route
    )
    await session.commit()
    return study


class TestWhetherAWorkerHoldsItNow:
    @pytest.mark.asyncio
    async def test_a_queued_study_is_not_being_worked_on(self, session, admin_user):
        study = await _study(session, admin_user)
        activity = await study_activity(session, study)
        assert activity == {"advancing": True, "working": False, "since": None}

    @pytest.mark.asyncio
    async def test_a_live_claim_is_work_under_way_since_it_was_taken(self, session, admin_user):
        study = await _study(session, admin_user)
        before = datetime.now(timezone.utc)
        await own.acquire(session, study.id, holder="driver")
        activity = await study_activity(session, study)
        assert activity["working"] is True
        assert datetime.fromisoformat(activity["since"]) >= before.replace(microsecond=0)

    @pytest.mark.asyncio
    async def test_an_expired_claim_is_not_work_under_way(self, session, admin_user):
        """A crashed worker's claim must not read as a study still being worked on."""
        study = await _study(session, admin_user)
        await own.acquire(session, study.id, holder="crashed", lease_seconds=-1)
        assert (await study_activity(session, study))["working"] is False


class TestTheStudyResponseCarriesIt:
    @pytest_asyncio.fixture(autouse=True)
    async def _enable(self, session):
        from app.services import beta_features_service

        await beta_features_service.set_flag(session, "lit_validation", True)
        await session.commit()

    @pytest.mark.asyncio
    async def test_the_route_chosen_at_the_button_is_in_the_response(self, client, session, admin_user, admin_token):
        """The schema declared it and the response never filled it, so the page could not tell a
        study that reads itself from one that waits for a click."""
        study = await _study(session, admin_user)
        r = await client.get(f"/api/validation-studies/{study.id}", headers={"Authorization": f"Bearer {admin_token}"})
        assert r.status_code == 200
        assert r.json()["intended_route"] == "deposit"

    @pytest.mark.asyncio
    async def test_the_response_says_the_read_is_under_way(self, client, session, admin_user, admin_token):
        study = await _study(session, admin_user)
        await own.acquire(session, study.id, holder="driver")
        r = await client.get(f"/api/validation-studies/{study.id}", headers={"Authorization": f"Bearer {admin_token}"})
        activity = r.json()["activity"]
        assert activity["advancing"] is True
        assert activity["working"] is True
        assert activity["since"]

    @pytest.mark.asyncio
    async def test_a_study_waiting_for_a_person_says_so(self, client, session, admin_user, admin_token):
        study = await _study(session, admin_user, route=None)
        r = await client.get(f"/api/validation-studies/{study.id}", headers={"Authorization": f"Bearer {admin_token}"})
        assert r.json()["activity"] == {"advancing": False, "working": False, "since": None}
