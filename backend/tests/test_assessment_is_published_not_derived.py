"""plan_8_5 sections 3.1 and 3.2: the score a reader sees is the one the study published.

Before this, the report derived the card, the list derived it again, and the snapshot the study
stored was written and never read. This holds the new contract: production settles and publishes,
every surface projects, and opening a report changes nothing.
"""

import pytest

from app.services.validation_assessment import run_assessment
from app.services.validation_report_summary import (
    compute_scorecard_record,
    record_evidence_assessment,
    summarize,
)
from app.services.validation_study_service import ValidationStudyService

_PLAN = {
    "reported_experiments": [{"id": "e1", "assay": "bulk RNA-seq", "organism": "Homo sapiens"}],
    "differential_design": {"contrasts": []},
    "sample_sheet": {"organism": "Homo sapiens", "sample_count": 4},
}


def _report(evidence, plan=_PLAN):
    return summarize(
        study={"state": "classified", "classification": "access_restricted"},
        evidence=evidence,
        plan=plan,
        targets=[],
        issues=[],
        checks=None,
    )


class TestTheReportShowsWhatWasPublished:
    def test_a_study_with_no_recorded_assessment_shows_no_v3_card(self):
        """A historical report is untouched: it reads exactly as it did before rubric v3 existed."""
        report = _report({})
        assert report["evidence_score"] is None
        assert report["scorecard"]["title"] != "Findings Scorecard"

    def test_a_recorded_assessment_is_what_the_card_shows(self):
        from app.services.validation_rubric_assessment import build_assessment

        assessment = build_assessment(plan=_PLAN, evidence={}, claims=[], inventory=None)
        card = _report({"rubric_assessment": assessment})["evidence_score"]
        assert card["score"] > 0
        assert card["assessment_revision"] == 1

    def test_the_card_follows_the_record_rather_than_the_evidence_beside_it(self):
        from app.services.validation_rubric_assessment import build_assessment

        assessment = build_assessment(plan=_PLAN, evidence={}, claims=[], inventory=None)
        published = assessment["outcomes"]["S1.A"]["outcome"]
        assessment["outcomes"]["S1.A"] = {"outcome": "undetermined", "rationale": "withdrawn", "scope": "s"}
        card = _report({"rubric_assessment": assessment})["evidence_score"]
        rows = {
            row["leaf"]: row
            for section in card["sections"]
            for criterion in section["criteria"]
            for row in criterion["obligations"]
        }
        assert published == "verified"
        assert rows["S1.A"]["outcome"] == "undetermined", "the report renders the published revision, not a new one"

    def test_rendering_a_report_records_nothing(self):
        evidence = {}
        _report(evidence)
        assert evidence == {}


class TestTheProductionPathPublishesIt:
    @pytest.mark.asyncio
    async def test_the_assessment_stage_records_an_assessment(self, session, admin_user):
        study = await ValidationStudyService.create_study(
            session, admin_user.organization_id, admin_user.id, source_accession="GSE1", intended_route="deposit"
        )
        study.state = "acquiring_processed"
        await session.flush()
        await run_assessment(session, study)
        held = (study.evidence_json or {}).get("rubric_assessment")
        assert held is not None
        assert held["revision"] == 1
        assert held["outcomes"]

    @pytest.mark.asyncio
    async def test_an_unchanged_study_keeps_the_revision_it_published(self, session, admin_user):
        study = await ValidationStudyService.create_study(
            session, admin_user.organization_id, admin_user.id, source_accession="GSE1", intended_route="deposit"
        )
        study.state = "acquiring_processed"
        await session.flush()
        await record_evidence_assessment(session, study, reason="first")
        first = study.evidence_json["rubric_assessment"]["at"]
        await record_evidence_assessment(session, study, reason="again")
        assert study.evidence_json["rubric_assessment"]["at"] == first
        assert study.evidence_json["rubric_assessment"]["revision"] == 1

    @pytest.mark.asyncio
    async def test_changed_evidence_publishes_a_new_revision_and_keeps_the_old_one(self, session, admin_user):
        study = await ValidationStudyService.create_study(
            session, admin_user.organization_id, admin_user.id, source_accession="GSE1", intended_route="deposit"
        )
        study.state = "acquiring_processed"
        await session.flush()
        await record_evidence_assessment(session, study, reason="first")
        study.evidence_json = {
            **study.evidence_json,
            "precompute_checks": {"species_matches": {"verdict": "mismatch", "detail": "the deposit records mouse"}},
        }
        await record_evidence_assessment(session, study, reason="the species check settled")
        held = study.evidence_json["rubric_assessment"]
        assert held["revision"] == 2
        history = study.evidence_json["rubric_assessment_history"]
        assert history[0]["revision"] == 1
        assert history[0]["superseded_because"] == "the species check settled"


class TestAStudyWithNoFindingInventoryStillPublishesItsScore:
    """plan_8_5 gate 1: a documentary score must not wait for a result inventory to exist."""

    @pytest.mark.asyncio
    async def test_the_snapshot_carries_the_evidence_score_with_no_inventory(self, session, admin_user):
        study = await ValidationStudyService.create_study(
            session, admin_user.organization_id, admin_user.id, source_accession="GSE1", intended_route="deposit"
        )
        study.state = "acquiring_processed"
        await session.flush()
        await record_evidence_assessment(session, study, reason="assessed")
        record = await compute_scorecard_record(session, study)
        assert record is not None
        assert record["evidence_score"]["score"] >= 0
        assert record["assessment_revision"] == 1
        assert "outcomes" not in record, "there is no finding inventory to score findings from"
