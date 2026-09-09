"""The route is chosen once, at the button, and the study runs itself from there.

**The defect this closes.** Clicking "Validate findings" created a study in `requested` and dropped
the user on its detail page showing "Requested - Step 1 of 9" with an in-progress badge. Nothing was
running. TWO manual clicks stood between that page and any work: "Read paper", and then "Approve"
with the route modal. Both looked like progress rather than a stop, so a study could sit untouched
indefinitely while the page implied it was working. That is exactly what happened to study 30.

**The shape of the fix** (owner decision, 2026-09-08): ask the route at the button, before anything
is read, and then let the driver carry the study through the read and onto the chosen route without
a second gate.

**Why the capability check survives anyway.** `discover_capabilities` needs the GEO accession, which
only exists once the paper has been READ, so the upfront modal cannot know whether a route is
possible. plan_7 step 13 and amendment 3 exist precisely so a user is not offered three
equal-looking routes on a paper that supports one. So the answer is not to drop that check but to
move it: the choice is taken upfront, and it is VALIDATED after the read. A choice that works
proceeds silently; a choice that cannot work stops at `plan_ready` and says why, which is the one
case where a person is genuinely needed.
"""

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.validation_study import ValidationStudy
from app.services.validation_driver_service import ValidationDriverService
from app.services.validation_study_service import ValidationStudyService


@pytest_asyncio.fixture
async def _no_llm(monkeypatch):
    """The front half is driven without spending on a model: `read_and_plan` is faked, because what
    is under test is the WIRE, not the extraction."""

    async def _read(session, study, org_id, user_id, full_text=None):
        study.state = "plan_ready"
        await session.flush()
        return study

    monkeypatch.setattr(ValidationDriverService, "read_and_plan", _read)


async def _study(session, admin_user, *, intended_route=None, evidence=None, state="requested"):
    study = await ValidationStudyService.create_study(
        session,
        admin_user.organization_id,
        admin_user.id,
        source_accession="GSE274331",
        intended_route=intended_route,
    )
    study.state = state
    if evidence is not None:
        study.evidence_json = evidence
    await session.flush()
    return study


def _caps(*, preprocessed="yes", raw="yes"):
    return {
        "capabilities": {
            "preprocessed_data": {"value": preprocessed},
            "raw_data": {"value": raw},
        }
    }


class TestTheChoiceIsRecordedAtTheButton:
    @pytest.mark.asyncio
    async def test_a_study_can_be_created_with_the_route_already_chosen(self, session, admin_user):
        study = await _study(session, admin_user, intended_route="pipeline")
        assert study.intended_route == "pipeline"

    @pytest.mark.asyncio
    async def test_a_study_created_without_one_keeps_todays_manual_gate(self, session, admin_user):
        """Other entry points still exist, and this must not silently start spending for them."""
        study = await _study(session, admin_user)
        assert study.intended_route is None


class TestTheDriverCarriesItThroughTheRead:
    @pytest.mark.asyncio
    async def test_a_requested_study_with_a_route_reads_itself(self, session, admin_user, _no_llm):
        """No "Read paper" click. That button is the first of the two hidden stops."""
        study = await _study(session, admin_user, intended_route="deposit")
        await ValidationDriverService.advance_active_studies(session)
        await session.refresh(study)
        assert study.state != "requested"

    @pytest.mark.asyncio
    async def test_a_requested_study_without_a_route_is_left_alone(self, session, admin_user, _no_llm):
        study = await _study(session, admin_user)
        await ValidationDriverService.advance_active_studies(session)
        await session.refresh(study)
        assert study.state == "requested"


class TestAWorkableChoiceProceedsWithoutAsking:
    @pytest.mark.asyncio
    async def test_the_deposit_route_is_approved_onto_its_own_state(self, session, admin_user, _no_llm):
        study = await _study(session, admin_user, intended_route="deposit", evidence=_caps(), state="plan_ready")
        await ValidationDriverService.advance_active_studies(session)
        await session.refresh(study)
        assert study.state == "acquiring_processed"
        assert study.evidence_json["route"] == "deposit"

    @pytest.mark.asyncio
    async def test_the_pipeline_route_is_approved_onto_its_own_state(self, session, admin_user, _no_llm):
        study = await _study(session, admin_user, intended_route="pipeline", evidence=_caps(), state="plan_ready")
        await ValidationDriverService.advance_active_studies(session)
        await session.refresh(study)
        assert study.state == "acquiring_data"

    @pytest.mark.asyncio
    async def test_the_approval_is_stamped_to_the_person_who_asked(self, session, admin_user, _no_llm):
        """An auto-approval is still someone's decision: they made it at the button, and the audit
        trail has to name them rather than showing an unattributed state change."""
        study = await _study(session, admin_user, intended_route="deposit", evidence=_caps(), state="plan_ready")
        await ValidationDriverService.advance_active_studies(session)
        await session.refresh(study)
        assert study.approved_by_user_id == admin_user.id
        assert study.approved_at is not None


class TestAChoiceThatCannotWorkStopsAndSaysWhy:
    """change_7.1 section 4 changed where these studies STOP.

    They used to be left at `plan_ready` for ever: re-examined every 30 seconds, never changing,
    and to a reader indistinguishable from a study waiting for someone to click approve. The
    refusal and its reason are unchanged and still recorded on `route_blocked`; what is new is that
    the study then reaches a stated outcome instead of holding.
    """

    @pytest.mark.asyncio
    async def test_deposit_chosen_on_a_paper_with_no_deposited_matrix_holds(self, session, admin_user, _no_llm):
        """The case the upfront modal cannot warn about, so it is caught here instead."""
        study = await _study(
            session,
            admin_user,
            intended_route="deposit",
            evidence=_caps(preprocessed="no"),
            state="plan_ready",
        )
        await ValidationDriverService.advance_active_studies(session)
        await session.refresh(study)
        assert study.state == "classified"
        assert study.classification == "access_restricted"
        blocked = study.evidence_json["route_blocked"]
        assert blocked["chosen"] == "deposit"
        assert "deposit" in blocked["reason"].lower() or "processed" in blocked["reason"].lower()

    @pytest.mark.asyncio
    async def test_pipeline_chosen_on_a_paper_with_no_raw_reads_holds(self, session, admin_user, _no_llm):
        study = await _study(
            session, admin_user, intended_route="pipeline", evidence=_caps(raw="no"), state="plan_ready"
        )
        await ValidationDriverService.advance_active_studies(session)
        await session.refresh(study)
        assert study.state == "classified"
        assert study.evidence_json["route_blocked"]["chosen"] == "pipeline"

    @pytest.mark.asyncio
    async def test_an_unknown_capability_still_proceeds(self, session, admin_user, _no_llm):
        """plan_7 amendment 3: UNKNOWN is not NO. Treating a GEO timeout as an established absence
        would hide a workable route behind a transient failure."""
        study = await _study(
            session,
            admin_user,
            intended_route="deposit",
            evidence=_caps(preprocessed="unknown"),
            state="plan_ready",
        )
        await ValidationDriverService.advance_active_studies(session)
        await session.refresh(study)
        assert study.state == "acquiring_processed"

    @pytest.mark.asyncio
    async def test_it_does_not_retry_the_blocked_route_every_tick(self, session, admin_user, _no_llm):
        """A study that has answered must not be re-decided on a 30s loop forever. It settles on
        one outcome and one recorded reason, and a second tick changes neither."""
        study = await _study(
            session,
            admin_user,
            intended_route="deposit",
            evidence=_caps(preprocessed="no"),
            state="plan_ready",
        )
        await ValidationDriverService.advance_active_studies(session)
        blocked_at = study.evidence_json["route_blocked"]["at"]
        await ValidationDriverService.advance_active_studies(session)
        await session.refresh(study)
        assert study.state == "classified"
        assert study.evidence_json["route_blocked"]["chosen"] == "deposit"
        # The second tick re-decided nothing: same refusal, same timestamp.
        assert study.evidence_json["route_blocked"]["at"] == blocked_at

    @pytest.mark.asyncio
    async def test_a_species_mismatch_still_blocks_the_auto_approval(self, session, admin_user, _no_llm):
        """plan_7 step 14: a species mismatch refuses on a FACT, and choosing the route upfront does
        not authorise aligning mouse data to a human genome."""
        evidence = _caps()
        evidence["precompute_checks"] = {
            "species_matches": {
                "verdict": "mismatch",
                "detail": "the plan says human; the deposit declares Mus musculus",
            }
        }
        study = await _study(session, admin_user, intended_route="deposit", evidence=evidence, state="plan_ready")
        await ValidationDriverService.advance_active_studies(session)
        await session.refresh(study)
        # Still refused, and still refused on the species FACT rather than on the route.
        assert study.state != "acquiring_processed"
        assert study.evidence_json["route_blocked"]["chosen"] == "deposit"
        assert "musculus" in study.evidence_json["route_blocked"]["reason"].lower()


class TestTheStudyStillReachesAPersonWhenItHasTo:
    @pytest.mark.asyncio
    async def test_a_plan_ready_study_with_no_intended_route_is_untouched(self, session, admin_user, _no_llm):
        """Today's manual C1 gate is preserved for every other entry point."""
        study = await _study(session, admin_user, evidence=_caps(), state="plan_ready")
        await ValidationDriverService.advance_active_studies(session)
        await session.refresh(study)
        assert study.state == "plan_ready"
        assert "route_blocked" not in (study.evidence_json or {})


class TestBothRoutes:
    @pytest.mark.asyncio
    async def test_both_creates_the_sibling_the_way_the_gate_does(self, session, admin_user, _no_llm):
        study = await _study(session, admin_user, intended_route="both", evidence=_caps(), state="plan_ready")
        await ValidationDriverService.advance_active_studies(session)
        await session.refresh(study)
        sibling_id = study.evidence_json.get("sibling_study_id")
        assert sibling_id
        sibling = (await session.execute(select(ValidationStudy).where(ValidationStudy.id == sibling_id))).scalar_one()
        assert sibling.evidence_json["route"] == "pipeline"
