"""plan_8 section 4: a concluded study's outcome records are stored, with the revisions they were read under.

When a study reaches ``classified`` its finding outcomes are recorded beside the inventory revision,
the selection revision and the rubric version they were computed under. While those still hold, the
report reads the record, so a later change to how evidence is normalized cannot rewrite a concluded
study's score. A newer inventory or selection revision reads the current evidence instead, and the
record it replaces is kept.
"""

import pytest

from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_report_summary import report_summary_for
from app.services.validation_study_service import ValidationStudyService
from tests.test_scorecard_surfaces import _seed


async def _conclude(session, admin_user, **kwargs):
    study = await _seed(session, admin_user, **kwargs)
    study.state = "comparing"
    study.classification = None
    await session.flush()
    await ValidationStudyService.transition(
        session, study.id, admin_user.organization_id, admin_user.id, "classified", classification="inconclusive"
    )
    await session.flush()
    return study


@pytest.mark.asyncio
async def test_concluding_a_study_records_its_outcomes_and_their_revisions(session, admin_user):
    study = await _conclude(session, admin_user)
    record = study.evidence_json["scorecard_record"]
    # plan_8_1 stage 4: the record keeps the version its inventory was established under.
    assert record["rubric_version"] == 2
    assert record["inventory_revision"] == 1
    assert record["analysis_selection_revision"] is None
    assert record["outcomes"]["F5"]["status"] == "discrepancy"
    assert record["compact"]["score_label"] == "67 / 100"
    assert record["at"]


@pytest.mark.asyncio
async def test_the_report_reads_the_record_while_its_revisions_hold(session, admin_user):
    study = await _conclude(session, admin_user)
    # The comparison evidence goes away; a concluded study's score does not move with it.
    evidence = dict(study.evidence_json)
    evidence.pop("classification_result")
    study.evidence_json = evidence
    await session.flush()
    card = (await report_summary_for(session, study, admin_user.organization_id))["scorecard"]
    assert card["score_label"] == "67 / 100"
    assert card["outcomes_recorded_at"] == study.evidence_json["scorecard_record"]["at"]


@pytest.mark.asyncio
async def test_a_newer_inventory_revision_reads_the_current_evidence(session, admin_user):
    study = await _conclude(session, admin_user)
    await ReproductionPlanService.revise_finding_importance(
        session,
        study.id,
        admin_user.organization_id,
        admin_user.id,
        finding_id="F5",
        category="supporting",
        rationale="It extends the main result.",
        reason="the reading overstated its role",
    )
    card = (await report_summary_for(session, study, admin_user.organization_id))["scorecard"]
    assert card["score_label"] == "80 / 100"
    assert card["outcomes_recorded_at"] is None


@pytest.mark.asyncio
async def test_a_study_that_is_not_concluded_reads_the_current_evidence(session, admin_user):
    study = await _seed(session, admin_user)
    study.state = "comparing"
    await session.flush()
    card = (await report_summary_for(session, study, admin_user.organization_id))["scorecard"]
    assert card["score_label"] == "67 / 100"
    assert card["outcomes_recorded_at"] is None
    assert "scorecard_record" not in (study.evidence_json or {})


@pytest.mark.asyncio
async def test_concluding_again_keeps_the_record_it_replaces(session, admin_user):
    study = await _conclude(session, admin_user)
    first = study.evidence_json["scorecard_record"]
    study.state = "comparing"
    await session.flush()
    await ValidationStudyService.transition(
        session, study.id, admin_user.organization_id, admin_user.id, "classified", classification="inconclusive"
    )
    await session.flush()
    assert study.evidence_json["scorecard_history"] == [first]
