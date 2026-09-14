"""plan_8_2 section 4.1 and owner decision 4: applicability governs the whole report.

Study 46 (a wet-lab mechanobiology paper: qRT-PCR, western blot, AFM, imaging) was classified
``missing_data`` because no sequencing accession was extracted, and its report followed a "Not applicable"
scorecard with sequencing, nf-core and reference failures. A paper outside bioAF's methods is not missing
data. Applicability is decided per experiment and check:

- a paper with no eligible finding says "No eligible findings were identified for bioAF's current
  validation methods", which does not establish that it has no quantitative analysis;
- its sequencing, nf-core and reference requirements are withheld as not applying, never shown as failures;
- a new read classifies it ``inconclusive`` (no new classification); an existing study moves from
  ``missing_data`` only through the on-request recovery, with its old classification kept in history;
- a mixed paper keeps its supported assessments and names its unsupported scope;
- a missing reference blocks only operations that need it.

Paper-shaped fixtures; nothing here is a rule about any one paper.
"""

import pytest

from app.models.audit_log import AuditLog
from app.services.validation_applicability import (
    NO_ELIGIBLE,
    NOT_APPLICABLE,
    PARTIAL,
    applicability,
)
from app.services.validation_report_summary import summarize

_UNAVAILABLE = {k: {"status": "unavailable", "reason": "x"} for k in ("qc_metric", "author_results")}
_AVAILABLE = {"author_results": {"status": "available", "reason": None}}
_WET_LAB = {
    "reported_experiments": [{"id": "e1", "assay": "qRT-PCR", "workflow": None}],
    "blockers": [
        "No sequencing data or high-throughput omics data was generated.",
        "No data accession or repository deposit is named in the paper.",
        "no nf-core equivalent for method: qRT-PCR",
        "Experiment e1 (qRT-PCR): the paper does not state its reference, so reanalysis from raw reads and QC "
        "metrics from a run are refused for it; bioAF never substitutes another reference. Checks that need no "
        "reference are unaffected.",
    ],
    "finding_inventory": {"status": "not_applicable", "findings": [], "reason": "no computational finding"},
}


class TestApplicability:
    def test_a_paper_whose_experiments_bioaf_has_no_method_for_is_not_applicable(self):
        found = applicability(_WET_LAB, [])
        assert found["status"] == NOT_APPLICABLE
        assert found["statement"].startswith(NO_ELIGIBLE)
        assert "does not establish that the paper contains no quantitative analysis" in found["statement"]
        assert found["limitation"] == "bioAF has no validation method for qRT-PCR (experiment e1)."
        assert found["experiments"][0]["supported"] is False

    def test_a_mixed_paper_keeps_its_supported_experiment_and_names_the_rest(self):
        plan = {
            "reported_experiments": [
                {"id": "e1", "assay": "bulk RNA-seq", "workflow": "nf-core/rnaseq"},
                {"id": "e2", "assay": "western blot", "workflow": None},
            ]
        }
        targets = [
            {"reported_experiment_id": "e1", "checks": _AVAILABLE},
            {"reported_experiment_id": "e2", "checks": _UNAVAILABLE},
        ]
        found = applicability(plan, targets)
        assert found["status"] == PARTIAL and found["statement"] is None
        assert [(e["id"], e["supported"]) for e in found["experiments"]] == [("e1", True), ("e2", False)]
        assert found["limitation"] == "bioAF has no validation method for western blot (experiment e2)."

    def test_a_supported_paper_with_no_deposit_is_applicable(self):
        plan = {"reported_experiments": [{"id": "e1", "assay": "bulk RNA-seq", "workflow": "nf-core/rnaseq"}]}
        assert applicability(plan, [])["status"] == "applicable"

    def test_a_claim_bioaf_can_check_makes_its_experiment_eligible_without_a_workflow(self):
        plan = {"reported_experiments": [{"id": "e1", "assay": "proteomics", "workflow": None}]}
        assert applicability(plan, [{"reported_experiment_id": "e1", "checks": _AVAILABLE}])["status"] == "applicable"

    def test_methods_too_thin_to_name_an_assay_are_not_called_outside_bioafs_methods(self):
        plan = {"reported_experiments": [], "blockers": ["insufficient method detail to identify an assay"]}
        assert applicability(plan, [])["status"] == "undetermined"


def _summary(plan=_WET_LAB, classification="missing_data"):
    return summarize(
        study={"state": "classified", "classification": classification},
        evidence={},
        plan=plan,
        targets=[],
        issues=[],
    )


class TestTheReport:
    def test_the_verdict_says_no_eligible_findings_and_nothing_about_sequencing_reads(self):
        summary = _summary()
        assert summary["applicability"]["status"] == NOT_APPLICABLE
        joined = " ".join(summary["summary"])
        assert NO_ELIGIBLE in joined
        assert "sequencing reads" not in joined
        assert summary["headline"]["key"] == "not_applicable"

    def test_sequencing_nf_core_and_reference_requirements_are_withheld_as_not_applying(self):
        blockers = _summary()["blockers"]
        assert blockers[0]["kind"] == "outside_methods"
        assert blockers[0]["text"] == "bioAF has no validation method for qRT-PCR (experiment e1)."
        withheld = [b for b in blockers if b["kind"] == "not_applicable"]
        assert len(withheld) == 4
        assert any("nf-core" in b["withheld"] for b in withheld)
        assert all("nf-core" not in b["text"] and "reference" not in b["text"] for b in withheld)


# ---- a new read, and the existing study through its recovery ----

from tests.test_failed_read import _complete, _read, llm, quiet_discovery  # noqa: E402,F401  (fixtures)

_WET_LAB_EXTRACTION = dict(
    accessions=[],
    method={"assay": "qRT-PCR", "tools": ["TaqMan"], "reference_build": ""},
    reported_experiments=[{"id": "e1", "assay": "qRT-PCR", "claim_indices": []}],
    claims=[],
    data_availability="none",
)


class TestANewRead:
    @pytest.mark.asyncio
    async def test_a_paper_outside_bioafs_methods_is_inconclusive_with_the_applicability_statement(
        self,
        session,
        admin_user,
        llm,  # noqa: F811
        quiet_discovery,  # noqa: F811
    ):
        llm(_complete(**_WET_LAB_EXTRACTION))
        study, plan = await _read(session, admin_user)
        assert (study.state, study.classification) == ("classified", "inconclusive")
        assert study.failure_reason.startswith(NO_ELIGIBLE)
        # A missing reference blocks only operations that need it: a qRT-PCR experiment needs none.
        assert not any("does not state its reference" in b for b in plan.blockers_json or [])

    @pytest.mark.asyncio
    async def test_a_supported_paper_with_no_accession_still_lacks_its_data(
        self,
        session,
        admin_user,
        llm,  # noqa: F811
        quiet_discovery,  # noqa: F811
    ):
        llm(_complete(accessions=[]))
        study, _plan = await _read(session, admin_user)
        assert study.classification == "missing_data"


async def _legacy_wet_lab(session, user):
    from app.services.reproduction_plan_service import ReproductionPlanService
    from app.services.validation_study_service import ValidationStudyService

    study = await ValidationStudyService.create_study(session, user.organization_id, user.id)
    plan = await ReproductionPlanService.create_plan(
        session, study, user.id, accessions=[], reported_experiments=_WET_LAB["reported_experiments"]
    )
    plan.blockers_json = list(_WET_LAB["blockers"])
    study.state = "classified"
    study.classification = "missing_data"
    study.failure_reason = "; ".join(_WET_LAB["blockers"])
    await session.commit()
    return study, plan


class TestTheExistingStudy:
    @pytest.mark.asyncio
    async def test_it_keeps_its_classification_until_a_person_asks_and_the_report_offers_the_recovery(
        self, session, admin_user
    ):
        from app.services.validation_recovery import preview_recovery
        from app.services.validation_report_summary import report_summary_for

        study, _plan = await _legacy_wet_lab(session, admin_user)
        summary = await report_summary_for(session, study, admin_user.organization_id)
        assert study.classification == "missing_data"
        assert summary["recovery"]["available"] is True
        preview = await preview_recovery(session, study)
        (action,) = [a for a in preview["actions"] if a["kind"] == "restate_outcome"]
        assert "inconclusive" in action["detail"] and "missing_data" in action["detail"]

    @pytest.mark.asyncio
    async def test_the_recovery_restates_it_as_inconclusive_and_keeps_the_old_classification(self, session, admin_user):
        from app.services.validation_recovery import run_recovery

        study, _plan = await _legacy_wet_lab(session, admin_user)
        result = await run_recovery(session, study, user_id=admin_user.id)
        await session.commit()
        assert result["restated"] == {"from": "missing_data", "to": "inconclusive"}
        assert study.classification == "inconclusive"
        assert study.failure_reason.startswith(NO_ELIGIBLE)
        prior = study.evidence_json["recovery_history"][-1]["prior"]
        assert prior["classification"] == "missing_data"
        assert prior["failure_reason"].startswith("No sequencing data")
        audit = (
            await session.execute(
                AuditLog.__table__.select().where(
                    AuditLog.entity_type == "validation_study",
                    AuditLog.entity_id == study.id,
                    AuditLog.action == "recovery",
                )
            )
        ).all()
        assert len(audit) == 1

    @pytest.mark.asyncio
    async def test_the_export_says_no_eligible_finding_was_identified_and_fails_nothing_on_sequencing(
        self, session, admin_user
    ):
        from app.services.provenance.data_gatherer import ProvenanceDataGatherer
        from app.services.provenance.json_renderer import JsonRenderer
        from app.services.provenance.markdown_renderer import MarkdownRenderer

        study, _plan = await _legacy_wet_lab(session, admin_user)
        data = await ProvenanceDataGatherer.gather_validation_study(session, study.id, admin_user.organization_id)
        text = MarkdownRenderer.render("validation_study", JsonRenderer.render("validation_study", data, "a@b.c"))
        assert "No eligible finding was identified for bioAF's current validation methods" in text
        assert "bioAF does not yet choose among a paper's findings automatically" not in text
        assert "The paper's deposits hold no raw sequencing reads" not in text
        blockers = text.split("**Blockers:**")[1].split("\n")[0]
        assert blockers.strip() == "bioAF has no validation method for qRT-PCR (experiment e1)."
        not_applying = text.split("**Requirements that do not apply to this paper:**")[1].split("\n")[0]
        assert "no nf-core equivalent for method: qRT-PCR" in not_applying

    @pytest.mark.asyncio
    async def test_a_supported_study_missing_its_data_is_not_offered_a_restatement(self, session, admin_user):
        from app.services.validation_recovery import preview_recovery

        study, plan = await _legacy_wet_lab(session, admin_user)
        plan.reported_experiments_json = [{"id": "e1", "assay": "bulk RNA-seq", "workflow": "nf-core/rnaseq"}]
        await session.commit()
        preview = await preview_recovery(session, study)
        assert "restate_outcome" not in [a["kind"] for a in preview["actions"]]
