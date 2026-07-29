"""Provenance report for a ValidationStudy (lit_validation A3 + F3).

A3 (paper -> experiment/run link) and F3 (export the study + its evidence bundle) are delivered by a
new ``validation_study`` provenance entity type reusing the existing ProvenanceReportService. The
report renders the full reproduction chain:

    source paper -> reproduction plan -> experiment -> data run (fetchngs) -> analysis run (rnaseq)

plus the computed-vs-claimed evidence and the classifier's verdict. The same linkage also surfaces in
reverse on the reproduction experiment's own provenance report ("this experiment reproduces paper X").
These tests pin the gatherer, the JSON/Markdown/CSV renderers, the experiment reverse-link, and the
HTTP endpoint (including RBAC).
"""

from __future__ import annotations

import json
import zipfile
from io import BytesIO

import pytest
import pytest_asyncio

from app.models.experiment import Experiment
from app.models.literature import LiteraturePaper
from app.models.pipeline_run import PipelineRun
from app.services.provenance.csv_renderer import CsvRenderer
from app.services.provenance.data_gatherer import ProvenanceDataGatherer
from app.services.provenance.json_renderer import JsonRenderer
from app.services.provenance.markdown_renderer import MarkdownRenderer
from app.services.provenance.pdf_renderer import PdfRenderer
from app.services.provenance.report_service import ProvenanceReportService
from app.services.provenance.schema import SCHEMA_VERSION
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_study_service import ValidationStudyService


@pytest_asyncio.fixture(autouse=True)
async def _enable_lit_validation(session):
    # The provenance HTTP endpoints live on the beta-gated validation router (spec-07); turn the flag on
    # so they are reachable (it defaults off -> 404).
    from app.services import beta_features_service

    await beta_features_service.set_flag(session, "lit_validation", True)
    await session.commit()


_DOI = "10.3390/jfb17020057"

# The classifier's evidence bundle for a bulk RNA-seq study reproduced end to end (mirrors the real
# GSE309060 study-3 shape: one pre-trim read-depth target agrees, one post-trim target has no computed
# counterpart -> thin-but-validated).
_EVIDENCE = {
    "computed_metrics": {
        "total_sequences": 6_677_908.0,
        "reads_mapped_genome": 0.955,
        "reads_mapped_genome_unique": 0.7769,
        "percent_gc": 41.2,
        "percent_duplicates": 74.3,
        "avg_sequence_length": 73.6,
        "total_samples": 4,
    },
    "comparison_targets": [
        {"metric_key": "mean_reads_per_sample_pre_trim", "claimed_value": 7_000_000.0, "unit": "reads"},
        {"metric_key": "mean_reads_after_trimming_per_sample", "claimed_value": 5_000_000.0, "unit": "reads"},
    ],
    "data_run_id": None,
    "analysis_run_id": None,
    "qc_dashboard_id": 15,
    "classification_result": {
        "comparisons": [
            {
                "metric_key": "mean_reads_per_sample_pre_trim",
                "mapped_key": "total_sequences",
                "claimed_value": 7_000_000.0,
                "claimed_normalized": 7_000_000.0,
                "computed_value": 6_677_908.0,
                "unit": "reads",
                "delta": -322_092.0,
                "within_tolerance": True,
                "verdict": "agree",
            },
            {
                "metric_key": "mean_reads_after_trimming_per_sample",
                "mapped_key": None,
                "claimed_value": 5_000_000.0,
                "claimed_normalized": None,
                "computed_value": None,
                "unit": "reads",
                "delta": None,
                "within_tolerance": None,
                "verdict": "not_computed",
            },
        ],
        "attribution": {"our_side": "n/a", "reasons": []},
        "coverage": {"targets": 2, "comparable": 1, "agree": 1, "diverge": 0, "not_computed": 1, "not_reported": 0},
        "classification": "validated",
        "auto_finalize": True,
        "reasoning": "1 comparable metric agrees with the paper within tolerance; 1 other claimed "
        "metric had no computed counterpart, so coverage is thin.",
    },
}


async def _seed_validation_study(session, user) -> dict:
    """A classified (validated) study with a source paper, plan+targets, experiment, and both runs."""
    org_id = user.organization_id

    paper = LiteraturePaper(
        organization_id=org_id,
        doi=_DOI,
        pmid="40000000",
        title="RNA-Seq of Gingival Fibroblasts Grown on Collagen Membranes and Hyaluronic Acid",
        title_normalized="rna-seq of gingival fibroblasts grown on collagen membranes and hyaluronic acid",
        authors_json=[{"name": "Panahipour L"}, {"name": "Huang X"}, {"name": "Gruber R"}],
        journal="J Funct Biomater",
        provenance="manual",
    )
    session.add(paper)
    await session.flush()

    exp = Experiment(
        organization_id=org_id,
        name="Reproduction: Panahipour GSE309060",
        owner_user_id=user.id,
        status="registered",
    )
    session.add(exp)
    await session.flush()

    data_run = PipelineRun(
        organization_id=org_id,
        experiment_id=exp.id,
        pipeline_name="nf-core/fetchngs",
        pipeline_version="1.12.0",
        status="completed",
        submitted_by_user_id=user.id,
    )
    analysis_run = PipelineRun(
        organization_id=org_id,
        experiment_id=exp.id,
        pipeline_name="nf-core/rnaseq",
        pipeline_version="3.14.0",
        status="completed",
        reference_genome="GRCh38",
        submitted_by_user_id=user.id,
    )
    session.add_all([data_run, analysis_run])
    await session.flush()

    study = await ValidationStudyService.create_study(
        session, org_id, user.id, paper_id=paper.id, source_doi=_DOI, source_accession="GSE309060"
    )
    plan = await ReproductionPlanService.create_plan(
        session,
        study,
        user.id,
        accessions=["GSE309060"],
        pipeline_key="nf-core/rnaseq",
        pipeline_version="3.14.0",
        reference_genome="GRCh38",
        reference_build="GENCODE 29",
        mapping_confidence="partial",
        mapping_notes="STAR + GRCh38 + GENCODE matches nf-core/rnaseq defaults.",
        blockers=[],
    )
    await ReproductionPlanService.add_comparison_targets(
        session,
        plan,
        [
            {
                "metric_key": "mean_reads_per_sample_pre_trim",
                "claimed_value": 7_000_000.0,
                "unit": "reads",
                "source_locator": "Methods",
            },
            {
                "metric_key": "mean_reads_after_trimming_per_sample",
                "claimed_value": 5_000_000.0,
                "unit": "reads",
                "source_locator": "Methods",
            },
        ],
    )

    study.experiment_id = exp.id
    study.data_run_id = data_run.id
    study.analysis_run_id = analysis_run.id
    study.state = "classified"
    study.classification = "validated"
    evidence = dict(_EVIDENCE)
    evidence["data_run_id"] = data_run.id
    evidence["analysis_run_id"] = analysis_run.id
    study.evidence_json = evidence
    await session.flush()

    return {
        "study_id": study.id,
        "paper_id": paper.id,
        "experiment_id": exp.id,
        "data_run_id": data_run.id,
        "analysis_run_id": analysis_run.id,
        "plan_id": plan.id,
    }


@pytest_asyncio.fixture
async def seeded_study(session, admin_user) -> dict:
    return await _seed_validation_study(session, admin_user)


@pytest_asyncio.fixture
async def study_json(session, seeded_study, admin_user) -> dict:
    data = await ProvenanceDataGatherer.gather_validation_study(
        session, seeded_study["study_id"], admin_user.organization_id
    )
    return JsonRenderer.render("validation_study", data, "admin@test.com")


# ---------------------------------------------------------------------------
# Gatherer
# ---------------------------------------------------------------------------


class TestGatherer:
    @pytest.mark.asyncio
    async def test_gathers_study_paper_plan_experiment_runs_evidence(self, session, seeded_study, admin_user):
        data = await ProvenanceDataGatherer.gather_validation_study(
            session, seeded_study["study_id"], admin_user.organization_id
        )
        assert data.study["id"] == seeded_study["study_id"]
        assert data.study["state"] == "classified"
        assert data.study["classification"] == "validated"
        assert data.study["confidence"] == 100.0

        # A3: the source paper is linked
        assert data.source_paper is not None
        assert data.source_paper["doi"] == _DOI
        assert "Gingival Fibroblasts" in data.source_paper["title"]

        assert data.reproduction_plan is not None
        assert data.reproduction_plan["pipeline_key"] == "nf-core/rnaseq"
        assert data.reproduction_plan["reference_genome"] == "GRCh38"
        assert len(data.comparison_targets) == 2

        assert data.experiment is not None
        assert data.experiment["id"] == seeded_study["experiment_id"]

        # A3: both runs, each labelled by its role in the reproduction chain
        by_role = {r["role"]: r for r in data.pipeline_runs}
        assert set(by_role) == {"data_acquisition", "analysis"}
        assert by_role["data_acquisition"]["pipeline_name"] == "nf-core/fetchngs"
        assert by_role["analysis"]["pipeline_name"] == "nf-core/rnaseq"

        assert data.evidence is not None
        assert data.evidence["classification_result"]["classification"] == "validated"
        assert len(data.audit_trail) >= 1

    @pytest.mark.asyncio
    async def test_respects_org_isolation(self, session, seeded_study, admin_user):
        data = await ProvenanceDataGatherer.gather_validation_study(
            session, seeded_study["study_id"], admin_user.organization_id + 999
        )
        assert data.study == {}


# ---------------------------------------------------------------------------
# JSON renderer
# ---------------------------------------------------------------------------


class TestJsonRenderer:
    def test_schema_and_report_type(self, study_json):
        assert study_json["schema_version"] == SCHEMA_VERSION
        assert study_json["report_type"] == "validation_study"
        assert study_json["generated_by"] == "admin@test.com"

    def test_entity_sections(self, study_json):
        entity = study_json["entity"]
        assert entity["type"] == "validation_study"
        assert entity["classification"] == "validated"
        assert entity["source_paper"]["doi"] == _DOI
        assert entity["reproduction_plan"]["pipeline_key"] == "nf-core/rnaseq"
        assert len(entity["reproduction_plan"]["comparison_targets"]) == 2
        assert entity["experiment"]["name"].startswith("Reproduction:")
        assert len(entity["pipeline_runs"]) == 2
        assert entity["evidence"]["classification_result"]["classification"] == "validated"
        assert "audit_trail" in study_json


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------


class TestMarkdownRenderer:
    def test_sections_present(self, study_json):
        md = MarkdownRenderer.render("validation_study", study_json)
        assert "## Source Paper" in md
        assert "## Reproduction Plan" in md
        assert "## Computed vs Claimed" in md
        assert "## Provenance Chain" in md
        assert "## Audit Trail" in md

    def test_paper_to_run_link_is_visible(self, study_json):
        """A3: the report shows the paper wired to the experiment and both runs."""
        md = MarkdownRenderer.render("validation_study", study_json)
        assert _DOI in md
        assert "nf-core/fetchngs" in md
        assert "nf-core/rnaseq" in md
        assert "validated" in md.lower()

    def test_evidence_shows_computed_and_claimed(self, study_json):
        md = MarkdownRenderer.render("validation_study", study_json)
        assert "mean_reads_per_sample_pre_trim" in md
        assert "agree" in md
        assert "not_computed" in md


def _report_with_level3():
    """A minimal validation_study report dict (JsonRenderer output shape) carrying Level-3 evidence."""
    return {
        "report_type": "validation_study",
        "schema_version": "1.0",
        "generated_at": "2026-07-24T00:00:00Z",
        "generated_by": "admin@test.com",
        "bioaf_version": "test",
        "entity": {
            "id": 3,
            "state": "comparing",
            "classification": "validated",
            "source_paper": {"title": "T"},
            "reproduction_plan": {
                "pipeline_key": "nf-core/rnaseq",
                "differential_design": {
                    "contrasts": [
                        {"name": "dex vs untreated", "test_samples": ["SRX1"], "reference_samples": ["SRX2"]}
                    ],
                    "thresholds": {"log2fc": 1.0, "padj": 0.05},
                },
                "finding_claim": {"kind": "gene", "confirmed": True, "finding_set": {"n_sig": 10}},
            },
            "evidence": {
                "classification_result": {"classification": "validated", "comparisons": []},
                "level3_result": {
                    "concordance": {
                        "kind": "gene",
                        "verdict": "agree",
                        "paper_n": 100,
                        "our_n": 90,
                        "overlap": 85,
                        "concordant": 82,
                        "directional_overlap_frac": 0.82,
                        "enrichment_p": 1e-30,
                        "notes": [],
                    },
                    "our_finding_set": {"n_sig": 90},
                },
            },
        },
        "audit_trail": [],
    }


class TestLevel3Report:
    """F3' (ADR-069): the exported report renders the differential-finding concordance evidence."""

    def test_markdown_renders_level3_concordance_section(self):
        md = MarkdownRenderer.render("validation_study", _report_with_level3())
        assert "## Level 3" in md
        assert "dex vs untreated" in md  # the contrast
        assert "reproduced" in md.lower()  # the agree verdict
        assert "100" in md and "90" in md  # paper set vs our set
        assert "82" in md  # directional overlap

    def test_markdown_omits_level3_when_absent(self):
        report = _report_with_level3()
        report["entity"]["evidence"].pop("level3_result")
        report["entity"]["reproduction_plan"].pop("differential_design")
        report["entity"]["reproduction_plan"].pop("finding_claim")
        md = MarkdownRenderer.render("validation_study", report)
        assert "## Level 3" not in md

    def test_markdown_renders_partial_concordance_verdict(self):
        # A strong-but-partial concordance renders a distinct "partially reproduced" verdict line.
        report = _report_with_level3()
        report["entity"]["classification"] = "partially_reproduced"
        report["entity"]["evidence"]["classification_result"]["classification"] = "partially_reproduced"
        report["entity"]["evidence"]["level3_result"]["concordance"].update(
            verdict="partial", concordant=79, paper_n=210, directional_overlap_frac=0.376
        )
        md = MarkdownRenderer.render("validation_study", report)
        assert "## Level 3" in md
        assert "partially reproduced" in md.lower()


# ---------------------------------------------------------------------------
# CSV renderer
# ---------------------------------------------------------------------------


class TestCsvRenderer:
    def test_comparison_and_runs_csvs(self, study_json):
        csvs = CsvRenderer.render("validation_study", study_json)
        assert "comparison_targets.csv" in csvs
        assert "pipeline_runs.csv" in csvs
        assert "mean_reads_per_sample_pre_trim" in csvs["comparison_targets.csv"]
        assert "nf-core/fetchngs" in csvs["pipeline_runs.csv"]
        assert "nf-core/rnaseq" in csvs["pipeline_runs.csv"]


# ---------------------------------------------------------------------------
# Report service (end-to-end formats)
# ---------------------------------------------------------------------------


class TestReportService:
    @pytest.mark.asyncio
    async def test_generate_json(self, session, seeded_study, admin_user):
        result = await ProvenanceReportService.generate(
            session=session,
            entity_type="validation_study",
            entity_id=seeded_study["study_id"],
            org_id=admin_user.organization_id,
            user_email="admin@test.com",
            format="json",
        )
        assert result.content_type == "application/json"
        payload = json.loads(result.content)
        assert payload["report_type"] == "validation_study"

    @pytest.mark.asyncio
    async def test_generate_markdown(self, session, seeded_study, admin_user):
        result = await ProvenanceReportService.generate(
            session=session,
            entity_type="validation_study",
            entity_id=seeded_study["study_id"],
            org_id=admin_user.organization_id,
            user_email="admin@test.com",
            format="md",
        )
        assert result.content_type == "text/markdown"
        assert "Reproduction Validation Report" in result.content

    @pytest.mark.asyncio
    async def test_generate_csv_zip(self, session, seeded_study, admin_user):
        result = await ProvenanceReportService.generate(
            session=session,
            entity_type="validation_study",
            entity_id=seeded_study["study_id"],
            org_id=admin_user.organization_id,
            user_email="admin@test.com",
            format="csv",
        )
        assert result.content_type == "application/zip"
        names = zipfile.ZipFile(BytesIO(result.content)).namelist()
        assert any(n.endswith("comparison_targets.csv") for n in names)

    def test_pdf_renders(self, study_json):
        pytest.importorskip("weasyprint")
        pdf = PdfRenderer.render("validation_study", study_json)
        assert pdf[:5] == b"%PDF-"


# ---------------------------------------------------------------------------
# A3 reverse link: the reproduction experiment's own report cites the paper
# ---------------------------------------------------------------------------


class TestExperimentReverseLink:
    @pytest.mark.asyncio
    async def test_experiment_gather_includes_validation(self, session, seeded_study, admin_user):
        data = await ProvenanceDataGatherer.gather_experiment(
            session, seeded_study["experiment_id"], admin_user.organization_id
        )
        assert data.validation is not None
        assert data.validation["study_id"] == seeded_study["study_id"]
        assert data.validation["source_doi"] == _DOI
        assert data.validation["classification"] == "validated"

    @pytest.mark.asyncio
    async def test_experiment_report_renders_source_paper(self, session, seeded_study, admin_user):
        data = await ProvenanceDataGatherer.gather_experiment(
            session, seeded_study["experiment_id"], admin_user.organization_id
        )
        report = JsonRenderer.render("experiment", data, "admin@test.com")
        assert report["entity"]["validation"]["source_doi"] == _DOI
        md = MarkdownRenderer.render("experiment", report)
        assert "Reproduces Paper" in md
        assert _DOI in md

    @pytest.mark.asyncio
    async def test_plain_experiment_has_no_validation(self, session, admin_user):
        exp = Experiment(
            organization_id=admin_user.organization_id, name="Plain", owner_user_id=admin_user.id, status="registered"
        )
        session.add(exp)
        await session.flush()
        data = await ProvenanceDataGatherer.gather_experiment(session, exp.id, admin_user.organization_id)
        assert data.validation is None


# ---------------------------------------------------------------------------
# HTTP endpoint (F3) + RBAC
# ---------------------------------------------------------------------------


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


class TestReportEndpoint:
    @pytest.mark.asyncio
    async def test_json_report(self, client, session, seeded_study, admin_token):
        await session.commit()
        r = await client.get(
            f"/api/validation-studies/{seeded_study['study_id']}/provenance/report?format=json",
            headers=_auth(admin_token),
        )
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/json"
        report = r.json()
        assert report["report_type"] == "validation_study"
        assert report["entity"]["source_paper"]["doi"] == _DOI

    @pytest.mark.asyncio
    async def test_markdown_report_is_attachment(self, client, session, seeded_study, admin_token):
        await session.commit()
        r = await client.get(
            f"/api/validation-studies/{seeded_study['study_id']}/provenance/report?format=md",
            headers=_auth(admin_token),
        )
        assert r.status_code == 200, r.text
        assert "text/markdown" in r.headers["content-type"]
        assert "attachment" in r.headers["content-disposition"]

    @pytest.mark.asyncio
    async def test_viewer_can_export(self, client, session, seeded_study, viewer_token):
        """A study a viewer can see, a viewer can export (gated lit_validation:view)."""
        await session.commit()
        r = await client.get(
            f"/api/validation-studies/{seeded_study['study_id']}/provenance/report?format=json",
            headers=_auth(viewer_token),
        )
        assert r.status_code == 200, r.text

    @pytest.mark.asyncio
    async def test_missing_study_is_404(self, client, session, admin_token):
        await session.commit()
        r = await client.get("/api/validation-studies/999999/provenance/report?format=json", headers=_auth(admin_token))
        assert r.status_code == 404
