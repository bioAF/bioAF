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
        findings = reconcile_contradictions(precompute_checks=self._CHECK_OK, blockers=[self._BLOCKER], supplements=[])
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
        _, remaining = apply_resolutions(precompute_checks=self._CHECK_OK, blockers=[self._BLOCKER], findings=findings)
        assert remaining == []

    def test_an_unsettleable_contradiction_is_reported_as_unresolved(self):
        findings = reconcile_contradictions(precompute_checks=self._CHECK_OK, blockers=[self._BLOCKER], supplements=[])
        assert findings[0]["status"] == UNRESOLVED

    def test_an_unresolved_contradiction_leaves_both_statements_standing_but_downgrades_the_claim(self):
        findings = reconcile_contradictions(precompute_checks=self._CHECK_OK, blockers=[self._BLOCKER], supplements=[])
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


# ---- change_7.3 sections 1 and 9: the assessment keeps a ledger and reports a failure once --------

import pathlib  # noqa: E402

from app.services.supplement_inventory import parse_jats_supplements  # noqa: E402
from app.services.validation_issue_service import ValidationIssueService  # noqa: E402

_GROFF_JATS = (pathlib.Path(__file__).parent / "fixtures" / "groff" / "fulltext_jats.xml").read_text()


class _NotFound(Exception):
    def __init__(self):
        super().__init__("Client error '404 Not Found' for url")
        self.response = type("R", (), {"status_code": 404})()


async def _with_attachments(session, admin_user, monkeypatch, fetch):
    monkeypatch.setattr("app.services.validation_assessment.deposit_bytes_fetcher", fetch)
    study = await _approved(session, admin_user, state="acquiring_processed")
    study.evidence_json = {
        **(study.evidence_json or {}),
        "pmcid": "PMC6771404",
        "supplements": parse_jats_supplements(_GROFF_JATS),
    }
    await session.flush()
    return study


class TestTheAssessmentRecordsRetrievalOnce:
    @pytest.mark.asyncio
    async def test_every_attempt_lands_in_the_ledger(self, session, admin_user, monkeypatch):
        async def _fail(_url):
            raise _NotFound()

        study = await _with_attachments(session, admin_user, monkeypatch, _fail)
        await run_assessment(session, study)
        ledger = study.evidence_json["retrieval_ledger"]
        assert [e["outcome"] for e in ledger] == ["not_found"] * len(ledger)
        assert len(ledger) >= 2

    @pytest.mark.asyncio
    async def test_a_failed_source_is_one_issue_naming_the_attachments(self, session, admin_user, monkeypatch):
        async def _fail(_url):
            raise _NotFound()

        study = await _with_attachments(session, admin_user, monkeypatch, _fail)
        await run_assessment(session, study)
        issues = await ValidationIssueService.list_for_study(session, study.id, admin_user.organization_id)
        failed = [i for i in issues if i["outcome"] == "retrieval_failed"]
        assert len(failed) == 1
        assert "Supplemental File S2" in failed[0]["message"]
        assert "404" not in failed[0]["message"]

    @pytest.mark.asyncio
    async def test_the_issue_carries_the_technical_detail(self, session, admin_user, monkeypatch):
        async def _fail(_url):
            raise _NotFound()

        study = await _with_attachments(session, admin_user, monkeypatch, _fail)
        await run_assessment(session, study)
        issues = await ValidationIssueService.list_for_study(session, study.id, admin_user.organization_id)
        detail = next(i for i in issues if i["outcome"] == "retrieval_failed")["technical_detail"]
        assert detail["http_status"] == 404
        assert detail["url"].endswith("/PMC6771404/supplementaryFiles")
        assert detail["attempts"] == len(study.evidence_json["retrieval_ledger"])
        assert detail["first_at"] and detail["last_at"]

    @pytest.mark.asyncio
    async def test_a_successful_retrieval_records_no_issue(self, session, admin_user, monkeypatch):
        import io
        import zipfile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("supp_gr.252981.119_Supplemental_File_1_embryo_metadata.txt", "sample\tsex\nE1\tXX\n")

        async def _ok(_url):
            return buffer.getvalue()

        study = await _with_attachments(session, admin_user, monkeypatch, _ok)
        await run_assessment(session, study)
        issues = await ValidationIssueService.list_for_study(session, study.id, admin_user.organization_id)
        assert not [i for i in issues if i["outcome"] == "retrieval_failed"]

    @pytest.mark.asyncio
    async def test_a_resumed_assessment_keeps_the_earlier_attempts(self, session, admin_user, monkeypatch):
        async def _fail(_url):
            raise _NotFound()

        study = await _with_attachments(session, admin_user, monkeypatch, _fail)
        await run_assessment(session, study)
        before = len(study.evidence_json["retrieval_ledger"])
        await run_assessment(session, study)
        assert len(study.evidence_json["retrieval_ledger"]) > before


# ---- change_7.3 section 6: say what reconciliation covered, and mark provisional findings ---------

from types import SimpleNamespace  # noqa: E402

from app.services.reproduction_plan_service import ReproductionPlanService  # noqa: E402

_CLAIM = {
    "metric_key": "total_samples",
    "claim_text": "The resulting data set includes 54 samples",
    "claimed_value": 54,
    "unit": "samples",
}


async def _with_plan(session, admin_user, *, supplements=None, targets=(_CLAIM,), passages=None):
    study = await _approved(session, admin_user, state="acquiring_processed")
    plan = await ReproductionPlanService.create_plan(session, study, admin_user.id, accessions=["GSE309060"])
    if targets:
        await ReproductionPlanService.add_comparison_targets(session, plan, [dict(t) for t in targets])
    evidence = {**(study.evidence_json or {}), "supplements": supplements or []}
    if passages is not None:
        evidence["paper_passages"] = passages
    study.evidence_json = evidence
    await session.flush()
    return study, plan


def _provider(monkeypatch, *, configured=True):
    async def _cfg(_session, _org):
        return SimpleNamespace(provider="anthropic", model="m", api_key=None) if configured else None

    monkeypatch.setattr("app.services.validation_assessment.llm_provider_config_service.get_active", _cfg)
    monkeypatch.setattr("app.services.validation_assessment.get_client", lambda _p: object())


def _binding(monkeypatch, rows=None, *, raises=None):
    calls: list[dict] = []

    async def _bind(claims, **kw):
        calls.append({"claims": claims, **kw})
        if raises:
            raise raises
        return rows if rows is not None else [{"claim_index": 0, "bound_key": None, "reason": "r", "confidence": 0.5}]

    monkeypatch.setattr("app.services.validation_reconciliation.bind_claims", _bind)
    return calls


class TestReconciliationSaysWhetherItRan:
    """`reconciled: false` stood for five different causes."""

    @pytest.mark.asyncio
    async def test_nothing_to_work_from_is_not_performed_with_that_reason(self, session, admin_user, monkeypatch):
        _provider(monkeypatch)
        calls = _binding(monkeypatch)
        study, _ = await _with_plan(session, admin_user)
        record = await run_assessment(session, study)
        assert record["reconciliation"]["status"] == "not_performed"
        assert "no attachments were inspected" in record["reconciliation"]["reason"]
        assert calls == []

    @pytest.mark.asyncio
    async def test_no_provider_is_not_performed_with_that_reason(self, session, admin_user, monkeypatch):
        _provider(monkeypatch, configured=False)
        study, _ = await _with_plan(session, admin_user, supplements=[self._S1])
        record = await run_assessment(session, study)
        assert record["reconciliation"]["status"] == "not_performed"
        assert "language model" in record["reconciliation"]["reason"]

    @pytest.mark.asyncio
    async def test_no_claims_is_not_performed_with_that_reason(self, session, admin_user, monkeypatch):
        _provider(monkeypatch)
        study, _ = await _with_plan(session, admin_user, supplements=[self._S1], targets=())
        record = await run_assessment(session, study)
        assert record["reconciliation"]["status"] == "not_performed"
        assert "claims" in record["reconciliation"]["reason"]

    @pytest.mark.asyncio
    async def test_no_plan_is_not_performed_with_that_reason(self, session, admin_user, monkeypatch):
        _provider(monkeypatch)
        study = await _approved(session, admin_user, state="acquiring_processed")
        study.evidence_json = {**(study.evidence_json or {}), "supplements": [self._S1]}
        await session.flush()
        record = await run_assessment(session, study)
        assert record["reconciliation"]["status"] == "not_performed"
        assert "plan" in record["reconciliation"]["reason"]

    @pytest.mark.asyncio
    async def test_a_failure_is_not_performed_and_an_issue(self, session, admin_user, monkeypatch):
        _provider(monkeypatch)
        _binding(monkeypatch, raises=RuntimeError("boom"))
        study, _ = await _with_plan(session, admin_user, supplements=[self._S1])
        record = await run_assessment(session, study)
        assert record["reconciliation"]["status"] == "not_performed"
        issues = await ValidationIssueService.list_for_study(session, study.id, admin_user.organization_id)
        assert any(i["outcome"] == "not_performed" for i in issues)

    @pytest.mark.asyncio
    async def test_a_stage_with_nothing_to_work_from_is_an_issue_too(self, session, admin_user, monkeypatch):
        _provider(monkeypatch)
        _binding(monkeypatch)
        study, _ = await _with_plan(session, admin_user)
        await run_assessment(session, study)
        issues = await ValidationIssueService.list_for_study(session, study.id, admin_user.organization_id)
        assert [i["outcome"] for i in issues if i["outcome"] == "not_performed"] == ["not_performed"]

    @pytest.mark.asyncio
    async def test_a_run_on_inspected_evidence_says_so(self, session, admin_user, monkeypatch):
        _provider(monkeypatch)
        _binding(monkeypatch)
        study, _ = await _with_plan(session, admin_user, supplements=[self._S1])
        record = await run_assessment(session, study)
        assert record["reconciliation"]["status"] == "performed"
        assert record["reconciliation"]["basis"] == "inspected_evidence"

    @pytest.mark.asyncio
    async def test_the_model_failing_inside_it_is_not_performed(self, session, admin_user, monkeypatch):
        from app.services.validation_classifier_service import BINDING_FAILED

        _provider(monkeypatch)
        _binding(
            monkeypatch,
            rows=[{"claim_index": 0, "bound_key": None, "reason": "x", "confidence": 0.0, "bound_by": BINDING_FAILED}],
        )
        study, _ = await _with_plan(session, admin_user, supplements=[self._S1])
        record = await run_assessment(session, study)
        assert record["reconciliation"]["status"] == "not_performed"

    _S1 = {
        "label": "Supplemental File S1",
        "filename": "s1.txt",
        "kind": "attachment",
        "resolved": True,
        "role": "sample_metadata",
        "row_count": 54,
        "columns": ["Sample", "Sex", "Karyotype"],
        "retrieval": {"status": "retrieved", "ledger": "R1"},
    }


class TestTheContradictionPassSaysWhetherItRan:
    @pytest.mark.asyncio
    async def test_a_pass_that_ran_records_checked_and_its_pairs(self, session, admin_user, monkeypatch):
        _provider(monkeypatch, configured=False)
        study, _ = await _with_plan(session, admin_user)
        record = await run_assessment(session, study)
        assert record["contradiction_pass"]["checked"] is True
        assert "samples_described_enough" in " ".join(record["contradiction_pass"]["pairs"])

    @pytest.mark.asyncio
    async def test_a_pass_that_raised_is_not_checked_and_is_an_issue(self, session, admin_user, monkeypatch):
        def _raise(**_kw):
            raise RuntimeError("consistency exploded")

        _provider(monkeypatch, configured=False)
        monkeypatch.setattr("app.services.validation_consistency.reconcile_contradictions", _raise)
        study, _ = await _with_plan(session, admin_user)
        record = await run_assessment(session, study)
        assert record["contradiction_pass"]["checked"] is False
        issues = await ValidationIssueService.list_for_study(session, study.id, admin_user.organization_id)
        assert any(i["outcome"] == "not_performed" and "contradiction" in i["step"] for i in issues)


class TestReadTimeChecksAreProvisionalUntilReRun:
    _CHECK = {
        CHECK_SAMPLES_DESCRIBED: {
            "check": CHECK_SAMPLES_DESCRIBED,
            "verdict": OK,
            "detail": "supplemental files list per-sample metadata",
            "decided_by": "model",
        }
    }

    @pytest.mark.asyncio
    async def test_a_read_time_verdict_keeps_the_paper_text_basis_while_nothing_was_inspected(
        self, session, admin_user, monkeypatch
    ):
        _provider(monkeypatch, configured=False)
        study, _ = await _with_plan(session, admin_user)
        study.evidence_json = {**study.evidence_json, "precompute_checks": self._CHECK}
        await session.flush()
        await run_assessment(session, study)
        assert study.evidence_json["precompute_checks"][CHECK_SAMPLES_DESCRIBED]["basis"] == "paper_text"

    @pytest.mark.asyncio
    async def test_an_inspected_sample_table_re_settles_it_on_inspected_evidence(
        self, session, admin_user, monkeypatch
    ):
        _provider(monkeypatch, configured=False)
        study, _ = await _with_plan(session, admin_user, supplements=[TestReconciliationSaysWhetherItRan._S1])
        study.evidence_json = {**study.evidence_json, "precompute_checks": self._CHECK}
        await session.flush()
        await run_assessment(session, study)
        check = study.evidence_json["precompute_checks"][CHECK_SAMPLES_DESCRIBED]
        assert check["basis"] == "inspected_evidence"
        assert "Supplemental File S1" in check["detail"]

    @pytest.mark.asyncio
    async def test_the_sample_data_check_is_re_run_against_the_deposits(self, session, admin_user, monkeypatch):
        _provider(monkeypatch, configured=False)
        study, plan = await _with_plan(session, admin_user)
        plan.sample_sheet_json = {"sample_count": 54}
        study.evidence_json = {
            **study.evidence_json,
            "capabilities": {
                **_RUNNABLE,
                "deposits": [
                    {
                        "archive": "ega",
                        "accession": "EGAS00001003667",
                        "exists": "yes",
                        "access": "controlled",
                        "supported": "no",
                        "registered_samples": 54,
                    }
                ],
            },
            "precompute_checks": {
                "sample_data_matches_paper": {
                    "check": "sample_data_matches_paper",
                    "verdict": UNKNOWN,
                    "detail": "no deposited files were listed to compare against",
                }
            },
        }
        await session.flush()
        await run_assessment(session, study)
        check = study.evidence_json["precompute_checks"]["sample_data_matches_paper"]
        assert "no deposited files were listed" not in check["detail"]
        assert "EGAS00001003667" in check["detail"]


# ---- change_7.3 section 7: typed blockers and the population guard --------------------------------

_STUDY_34_BLOCKER = (
    "Sample IDs assigned to each differential group (aneuploid/euploid, XX/XY, AA/CC, morphokinetic) are not "
    "explicitly enumerated in the text"
)


class TestTypedBlockersReachTheConsistencyPass:
    """The pass matched blockers with two regexes, and neither matched study 34's paraphrase, so the
    pair went unseen and `[]` came back."""

    _CHECK_OK = {CHECK_SAMPLES_DESCRIBED: {"check": CHECK_SAMPLES_DESCRIBED, "verdict": OK, "detail": "described"}}

    def test_a_legacy_blocker_saying_it_is_not_enumerated_is_caught_by_the_fallback(self):
        """A plan recorded before blockers had kinds. Study 34's resume on the deployed build found no
        contradiction because the fallback regexes did not know "not explicitly enumerated"."""
        findings = reconcile_contradictions(
            precompute_checks=self._CHECK_OK, blockers=[_STUDY_34_BLOCKER], supplements=[]
        )
        assert findings and findings[0]["status"] == UNRESOLVED

    def test_a_typed_blocker_is_detected_whatever_its_wording(self):
        findings = reconcile_contradictions(
            precompute_checks=self._CHECK_OK,
            blockers=[_STUDY_34_BLOCKER],
            supplements=[],
            blocker_kinds=[{"text": _STUDY_34_BLOCKER, "kind": "sample_assignment"}],
        )
        assert findings and findings[0]["status"] == UNRESOLVED

    def test_an_unretrieved_metadata_attachment_is_named_as_where_the_answer_may_be(self):
        s1 = {
            "label": "Supplemental File S1",
            "filename": "supp_Supplemental_File_1_embryo_metadata.txt",
            "references": ["Supplemental File S1"],
            "kind": "attachment",
            "resolved": False,
        }
        findings = reconcile_contradictions(
            precompute_checks=self._CHECK_OK,
            blockers=[_STUDY_34_BLOCKER],
            supplements=[s1],
            blocker_kinds=[{"text": _STUDY_34_BLOCKER, "kind": "sample_assignment"}],
        )
        assert "Supplemental File S1 may carry them" in findings[0]["outcome"]
        assert "not retrieved in this attempt" in findings[0]["outcome"]

    def test_a_blocker_typed_as_something_else_is_not_matched_by_the_regex(self):
        blocker = "per-sample assignments cannot be reconstructed from the deposit"
        findings = reconcile_contradictions(
            precompute_checks=self._CHECK_OK,
            blockers=[blocker],
            supplements=[],
            blocker_kinds=[{"text": blocker, "kind": "data_access"}],
        )
        assert findings == []


class TestAPostQcCountEqualToTheInventoryIsUnresolved:
    @pytest.mark.asyncio
    async def test_it_is_marked_and_never_rewritten(self, session, admin_user, monkeypatch):
        from sqlalchemy import select

        from app.models.comparison_target import ComparisonTarget

        _provider(monkeypatch, configured=False)
        study, plan = await _with_plan(
            session,
            admin_user,
            targets=(
                {
                    "metric_key": "total_samples",
                    "claim_text": "The resulting data set includes 35 WE samples, 19 TE biopsies",
                    "claimed_value": 54,
                    "unit": "RNA-seq samples",
                    "qc_stage": "post-QC",
                    "output_type": "count",
                },
            ),
            passages={"claims": [], "statements": ["three TE biopsy samples were excluded after quality control"]},
        )
        study.evidence_json = {
            **study.evidence_json,
            "capabilities": {**_RUNNABLE, "deposits": [{"accession": "EGAS00001003667", "registered_samples": 54}]},
        }
        await session.flush()
        await run_assessment(session, study)
        target = (
            await session.execute(select(ComparisonTarget).where(ComparisonTarget.reproduction_plan_id == plan.id))
        ).scalar_one()
        assert target.claimed_value == 54
        assert "EGAS00001003667" in target.unresolved_reason
        assert "excluded" in target.unresolved_reason

    @pytest.mark.asyncio
    async def test_a_count_tagged_as_collected_is_left_alone(self, session, admin_user, monkeypatch):
        from sqlalchemy import select

        from app.models.comparison_target import ComparisonTarget

        _provider(monkeypatch, configured=False)
        study, plan = await _with_plan(
            session,
            admin_user,
            targets=({**_CLAIM, "qc_stage": "as collected", "output_type": "count"},),
        )
        study.evidence_json = {
            **study.evidence_json,
            "capabilities": {**_RUNNABLE, "deposits": [{"accession": "EGAS00001003667", "registered_samples": 54}]},
        }
        await session.flush()
        await run_assessment(session, study)
        target = (
            await session.execute(select(ComparisonTarget).where(ComparisonTarget.reproduction_plan_id == plan.id))
        ).scalar_one()
        assert target.unresolved_reason is None


class TestALegacyRecordedFailureSeedsTheLedger:
    """change_7.3 deployed acceptance, step 4: a resumed study's ledger must show the original
    failure and the new attempt. Study 34's 404 predates the ledger and lived only as a copy on each
    row, so a successful retry cleared it and the ledger showed only the new attempt."""

    @pytest.mark.asyncio
    async def test_the_recorded_404_and_the_new_attempt_both_appear(self, session, admin_user, monkeypatch):
        import io
        import json as _json
        import zipfile

        persisted = _json.loads(
            (pathlib.Path(__file__).parent / "fixtures" / "groff" / "study_34_persisted.json").read_text()
        )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("supp_gr.252981.119_Supplemental_File_1_embryo_metadata.txt", "sample\tsex\nE1\tXX\n")

        async def _ok(_url):
            return buffer.getvalue()

        monkeypatch.setattr("app.services.validation_assessment.deposit_bytes_fetcher", _ok)
        study = await _approved(session, admin_user, state="acquiring_processed")
        study.evidence_json = {
            **(study.evidence_json or {}),
            "pmcid": "PMC6771404",
            "supplements": persisted["evidence"]["supplements"],
            "assessment": persisted["evidence"]["assessment"],
        }
        await session.flush()
        await run_assessment(session, study)
        ledger = study.evidence_json["retrieval_ledger"]
        assert [e["outcome"] for e in ledger] == ["not_found", "retrieved"]
        assert ledger[0]["http_status"] == 404
        assert ledger[0]["recorded_before_ledger"] is True
        assert ledger[0]["at"] == persisted["evidence"]["assessment"]["at"]


class TestAnAnalysedCountEqualToTheInventoryIsUnresolvedWhenThePaperExcludedSamples:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("statements, marked", [(["three TE biopsies were excluded after QC"], True), ([], False)])
    async def test_it_is_marked_only_when_exclusions_are_stated(
        self, session, admin_user, monkeypatch, statements, marked
    ):
        from sqlalchemy import select

        from app.models.comparison_target import ComparisonTarget

        _provider(monkeypatch, configured=False)
        study, plan = await _with_plan(
            session,
            admin_user,
            targets=({**_CLAIM, "qc_stage": "as analysed", "output_type": "count"},),
            passages={"claims": [], "statements": statements},
        )
        study.evidence_json = {
            **study.evidence_json,
            "capabilities": {**_RUNNABLE, "deposits": [{"accession": "EGAS00001003667", "registered_samples": 54}]},
        }
        await session.flush()
        await run_assessment(session, study)
        target = (
            await session.execute(select(ComparisonTarget).where(ComparisonTarget.reproduction_plan_id == plan.id))
        ).scalar_one()
        assert (target.unresolved_reason is not None) is marked
