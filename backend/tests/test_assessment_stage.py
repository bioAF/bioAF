"""change_7.2 section 4: an assessment record on every authorized study, on both routes.

change_7.1 built supplement resolution, retrieval propagation, reconciliation and evidence-based
completion, and attached every one of them to `_finish_without_execution`, which only a refused route
ever reached. Study 33, the first approved by hand, walked past all of it. The improvements were
delivered to exactly one path.

**Assessment completion is not execution completion.** The stage always produces a record. Where
execution is possible a terminal classification follows execution; where it is not, the record and
its stated limitation ARE the terminal outcome. A study that can still run must not be classified
here.
"""

import pytest

from app.services.validation_assessment import run_assessment
from app.services.validation_consistency import RESOLVED, UNRESOLVED, apply_resolutions, reconcile_contradictions
from app.services.validation_driver_service import ValidationDriverService
from app.services.validation_precompute_checks import CHECK_SAMPLES_DESCRIBED, OK, UNKNOWN
from app.services.validation_study_service import ValidationStudyService

_RUNNABLE = {
    "preprocessed_data": {"value": "yes"},
    "raw_data": {"value": "yes"},
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


async def _approved(session, admin_user, *, state, route="deposit"):
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_accession="GSE309060", intended_route=route
    )
    study.state = state
    study.evidence_json = {"capabilities": _RUNNABLE}
    await session.flush()
    return study


class TestTheStageRunsOnARouteThatCanStillSucceed:
    @pytest.mark.asyncio
    async def test_the_deposit_route_produces_an_assessment_record(self, session, admin_user):
        study = await _approved(session, admin_user, state="acquiring_processed")
        await ValidationDriverService._advance_one(session, study)
        assert (study.evidence_json or {}).get("assessment") is not None

    @pytest.mark.asyncio
    async def test_the_raw_read_route_produces_one_too(self, session, admin_user):
        study = await _approved(session, admin_user, state="acquiring_data", route="pipeline")
        await ValidationDriverService._advance_one(session, study)
        assert (study.evidence_json or {}).get("assessment") is not None

    @pytest.mark.asyncio
    async def test_a_study_that_can_still_run_is_not_classified_by_the_stage(self, session, admin_user):
        """A terminal classification follows execution where execution is possible."""
        study = await _approved(session, admin_user, state="acquiring_processed")
        await ValidationDriverService._advance_one(session, study)
        assert study.state == "acquiring_processed"
        assert study.classification is None

    @pytest.mark.asyncio
    async def test_the_stage_runs_before_the_acquisition_it_precedes(self, session, admin_user):
        study = await _approved(session, admin_user, state="acquiring_processed")
        await ValidationDriverService._advance_one(session, study)
        assert "deposit" not in (study.evidence_json or {})

    @pytest.mark.asyncio
    async def test_it_runs_once_rather_than_on_every_tick(self, session, admin_user):
        study = await _approved(session, admin_user, state="acquiring_processed")
        await ValidationDriverService._advance_one(session, study)
        first = study.evidence_json["assessment"]["at"]
        await ValidationDriverService._advance_one(session, study)
        assert study.evidence_json["assessment"]["at"] == first

    @pytest.mark.asyncio
    async def test_the_record_says_what_was_inspected(self, session, admin_user):
        study = await _approved(session, admin_user, state="acquiring_processed")
        record = await run_assessment(session, study)
        assert "supplements_inspected" in record
        assert "reconciled" in record


class TestReconciliationSettlesItsDependents:
    """The live contradiction: a check asserting per-sample assignments are recoverable beside a
    blocker asserting they cannot be reconstructed. Two model calls, one report, nothing comparing
    them. Shipping with both standing is not acceptable."""

    _CHECK_OK = {CHECK_SAMPLES_DESCRIBED: {"check": CHECK_SAMPLES_DESCRIBED, "verdict": OK, "detail": "described"}}
    _BLOCKER = "per-sample assignments cannot be reconstructed from the deposit"
    _SAMPLE_TABLE = {
        "label": "Supplemental File S1",
        "filename": "s1.txt",
        "resolved": True,
        "role": "sample_metadata",
        "row_count": 54,
    }

    def test_the_contradiction_is_detected(self):
        findings = reconcile_contradictions(
            precompute_checks=self._CHECK_OK, blockers=[self._BLOCKER], supplements=[]
        )
        assert findings

    def test_evidence_settles_it_when_the_sample_table_was_read(self):
        findings = reconcile_contradictions(
            precompute_checks=self._CHECK_OK, blockers=[self._BLOCKER], supplements=[self._SAMPLE_TABLE]
        )
        assert findings[0]["status"] == RESOLVED
        assert "54" in findings[0]["settled_by"]

    def test_a_resolved_contradiction_removes_the_superseded_blocker(self):
        findings = reconcile_contradictions(
            precompute_checks=self._CHECK_OK, blockers=[self._BLOCKER], supplements=[self._SAMPLE_TABLE]
        )
        _, remaining = apply_resolutions(
            precompute_checks=self._CHECK_OK, blockers=[self._BLOCKER], findings=findings
        )
        assert remaining == []

    def test_an_unsettleable_contradiction_is_reported_as_unresolved(self):
        findings = reconcile_contradictions(
            precompute_checks=self._CHECK_OK, blockers=[self._BLOCKER], supplements=[]
        )
        assert findings[0]["status"] == UNRESOLVED

    def test_an_unresolved_contradiction_leaves_both_statements_standing_but_downgrades_the_claim(self):
        findings = reconcile_contradictions(
            precompute_checks=self._CHECK_OK, blockers=[self._BLOCKER], supplements=[]
        )
        checks, remaining = apply_resolutions(
            precompute_checks=self._CHECK_OK, blockers=[self._BLOCKER], findings=findings
        )
        assert remaining == [self._BLOCKER]
        assert checks[CHECK_SAMPLES_DESCRIBED]["verdict"] == UNKNOWN
        assert checks[CHECK_SAMPLES_DESCRIBED]["unresolved_contradiction"] is True

    def test_agreement_produces_no_finding(self):
        findings = reconcile_contradictions(
            precompute_checks=self._CHECK_OK,
            blockers=["the paper names no nf-core equivalent"],
            supplements=[self._SAMPLE_TABLE],
        )
        assert findings == []

    @pytest.mark.asyncio
    async def test_the_stage_records_the_contradictions_it_found(self, session, admin_user):
        from app.services.reproduction_plan_service import ReproductionPlanService

        study = await _approved(session, admin_user, state="acquiring_processed")
        plan = await ReproductionPlanService.create_plan(
            session, study, admin_user.id, accessions=["GSE309060"], pipeline_key="nf-core/rnaseq"
        )
        plan.blockers_json = [self._BLOCKER]
        study.evidence_json = {
            **(study.evidence_json or {}),
            "precompute_checks": self._CHECK_OK,
            "supplements": [self._SAMPLE_TABLE],
        }
        await session.flush()

        record = await run_assessment(session, study)
        assert record["contradictions"]
        assert record["contradictions"][0]["status"] == RESOLVED
        assert plan.blockers_json == []
