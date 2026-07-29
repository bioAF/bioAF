"""Level-3 activation wiring (ADR-069 / spec-08): assemble evidence["level3"] from the ratified plan.

`build_level3_inputs` is the front-half glue that turns a Level-2 study into a Level-3 one. It joins
the B2e differential design + the B4 confirmed finding claim (on the plan) with the analysis run's
count-matrix file and the matching headless template, producing the dict the driver's `reproducing`
state consumes. Any missing piece degrades honestly to Level-2 (returns None), never a fabricated run.
"""

import pytest
import pytest_asyncio

from app.models.file import File
from app.models.pipeline_run import PipelineRun
from app.models.template_notebook import TemplateNotebook
from app.services.qc_dashboard_service import QCDashboardService
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_driver_service import ValidationDriverService
from app.services.validation_level3_service import build_level3_inputs
from app.services.validation_study_service import ValidationStudyService

_DESIGN = {
    "contrasts": [
        {
            "name": "dex vs untreated",
            "test_condition": "dex",
            "reference_condition": "untreated",
            "test_samples": ["SRX1", "SRX2"],
            "reference_samples": ["SRX3", "SRX4"],
        }
    ],
    "thresholds": {"log2fc": 1.0, "padj": 0.05},
}

_CLAIM = {
    "kind": "gene",
    "namespace": "symbol",
    "confirmed": True,
    "thresholds": {"log2fc": 1.0, "padj": 0.05},
    "finding_set": {"kind": "gene", "namespace": "symbol", "n_sig": 2, "entities": [{"id": "A1BG", "direction": "up"}]},
}


@pytest_asyncio.fixture
async def analysis_run(session, admin_user):
    run = PipelineRun(
        organization_id=admin_user.organization_id,
        submitted_by_user_id=admin_user.id,
        pipeline_name="nf-core/rnaseq",
        pipeline_version="3.14.0",
        status="completed",
    )
    session.add(run)
    await session.flush()
    return run


@pytest_asyncio.fixture
async def de_template(session, admin_user):
    tmpl = TemplateNotebook(
        organization_id=admin_user.organization_id,
        name="Differential Expression (DESeq2, headless)",
        category="differential_expression",
        notebook_path="notebooks/de_bulk_deseq2.ipynb",
        parameters_json={"id_column": "gene_id"},
        compatible_with="nf-core/rnaseq",
        is_builtin=True,
    )
    session.add(tmpl)
    await session.flush()
    return tmpl


async def _count_matrix_file(session, admin_user, run, filename="salmon.merged.gene_counts.tsv"):
    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://bucket/x.tsv",
        storage_uri="gs://bucket/x.tsv",
        filename=filename,
        file_type="count_matrix",
        source_pipeline_run_id=run.id,
    )
    session.add(f)
    await session.flush()
    return f


async def _study_with_plan(session, admin_user, run, *, design=_DESIGN, claim=_CLAIM):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    study.analysis_run_id = run.id
    await session.flush()
    plan = await ReproductionPlanService.create_plan(
        session, study, admin_user.id, pipeline_key="nf-core/rnaseq", differential_design=design
    )
    plan.finding_claim_json = claim
    await session.flush()
    return study, plan


@pytest.mark.asyncio
async def test_build_level3_inputs_assembles_full_gene_bundle(session, admin_user, analysis_run, de_template):
    f = await _count_matrix_file(session, admin_user, analysis_run)
    # A decoy template shares the differential_expression category (the interactive scRNA DE notebook,
    # seeded id 4 on the demo). It must NOT be selected: only the headless de_bulk_deseq2 template runs.
    session.add(
        TemplateNotebook(
            organization_id=admin_user.organization_id,
            name="scRNA DE (interactive)",
            category="differential_expression",
            notebook_path="notebooks/04_differential_expression.ipynb",
            parameters_json={},
            is_builtin=True,
        )
    )
    await session.flush()
    study, plan = await _study_with_plan(session, admin_user, analysis_run)

    level3 = await build_level3_inputs(session, study, plan)

    assert level3 is not None
    assert level3["template_id"] == de_template.id
    assert level3["input_file_ids"] == [f.id]
    assert level3["kind"] == "gene"
    assert level3["paper_finding_set"]["n_sig"] == 2
    params = level3["parameters"]
    assert params["counts_path"].startswith("/data/")
    assert params["counts_path"].endswith("salmon.merged.gene_counts.tsv")
    assert params["test_samples"] == "SRX1,SRX2"
    assert params["reference_samples"] == "SRX3,SRX4"
    assert params["lfc_threshold"] == 1.0
    assert params["padj_threshold"] == 0.05
    assert params["id_column"] == "gene_id"


@pytest_asyncio.fixture
async def da_template(session, admin_user):
    tmpl = TemplateNotebook(
        organization_id=admin_user.organization_id,
        name="Differential Accessibility (DESeq2, headless)",
        category="differential_accessibility",
        notebook_path="notebooks/da_peaks_deseq2.ipynb",
        parameters_json={},
        compatible_with="nf-core/atacseq",
        is_builtin=True,
    )
    session.add(tmpl)
    await session.flush()
    return tmpl


_INTERVAL_DESIGN = {
    "contrasts": [{"name": "KO vs WT", "test_samples": ["S1", "S2"], "reference_samples": ["S3", "S4"]}],
    "thresholds": {"log2fc": 1.0, "padj": 0.05},
}
_INTERVAL_CLAIM = {
    "kind": "interval",
    "namespace": "interval",
    "confirmed": True,
    "thresholds": {"log2fc": 1.0, "padj": 0.05},
    "finding_set": {
        "kind": "interval",
        "namespace": "interval",
        "n_sig": 2,
        "entities": [{"id": "chr1:1000-2000", "direction": "up"}],
    },
}

# The real nf-core/atacseq|chipseq consensus-peak count matrix filename.
_NFCORE_CONSENSUS = "consensus_peaks.mLb.clN.featureCounts.txt"


@pytest.mark.asyncio
async def test_build_level3_inputs_assembles_interval_bundle(session, admin_user, analysis_run, da_template):
    f = await _count_matrix_file(session, admin_user, analysis_run, filename=_NFCORE_CONSENSUS)
    study, plan = await _study_with_plan(
        session, admin_user, analysis_run, design=_INTERVAL_DESIGN, claim=_INTERVAL_CLAIM
    )

    level3 = await build_level3_inputs(session, study, plan)

    assert level3 is not None
    assert level3["template_id"] == da_template.id  # the headless DA template, keyed by notebook_path
    assert level3["kind"] == "interval"
    assert level3["input_file_ids"] == [f.id]
    params = level3["parameters"]
    assert params["counts_path"].endswith(_NFCORE_CONSENSUS)
    assert params["test_samples"] == "S1,S2"
    # The DA template takes no id_column (intervals are keyed by coordinates, not an id column).
    assert "id_column" not in params


@pytest.mark.asyncio
async def test_interval_count_matrix_heuristic_rejects_non_consensus_files(
    session, admin_user, analysis_run, da_template
):
    # A non-consensus output from the same run must NOT be mistaken for the DA count matrix.
    await _count_matrix_file(session, admin_user, analysis_run, filename="multiqc_report.html")
    study, plan = await _study_with_plan(
        session, admin_user, analysis_run, design=_INTERVAL_DESIGN, claim=_INTERVAL_CLAIM
    )
    assert await build_level3_inputs(session, study, plan) is None


@pytest.mark.asyncio
async def test_build_level3_inputs_none_without_finding_claim(session, admin_user, analysis_run, de_template):
    await _count_matrix_file(session, admin_user, analysis_run)
    study, plan = await _study_with_plan(session, admin_user, analysis_run, claim=None)
    assert await build_level3_inputs(session, study, plan) is None


@pytest.mark.asyncio
async def test_build_level3_inputs_none_when_claim_unconfirmed(session, admin_user, analysis_run, de_template):
    await _count_matrix_file(session, admin_user, analysis_run)
    unconfirmed = {**_CLAIM, "confirmed": False}
    study, plan = await _study_with_plan(session, admin_user, analysis_run, claim=unconfirmed)
    assert await build_level3_inputs(session, study, plan) is None


@pytest.mark.asyncio
async def test_build_level3_inputs_none_without_design_contrast(session, admin_user, analysis_run, de_template):
    await _count_matrix_file(session, admin_user, analysis_run)
    study, plan = await _study_with_plan(session, admin_user, analysis_run, design={"contrasts": [], "thresholds": {}})
    assert await build_level3_inputs(session, study, plan) is None


@pytest.mark.asyncio
async def test_build_level3_inputs_none_without_count_matrix(session, admin_user, analysis_run, de_template):
    # A file from the run exists but is not the count matrix -> honest Level-2 degrade.
    await _count_matrix_file(session, admin_user, analysis_run, filename="multiqc_report.html")
    study, plan = await _study_with_plan(session, admin_user, analysis_run)
    assert await build_level3_inputs(session, study, plan) is None


@pytest.mark.asyncio
async def test_build_level3_inputs_none_without_template(session, admin_user, analysis_run):
    # No builtin template registered for the org -> Level-2.
    await _count_matrix_file(session, admin_user, analysis_run)
    study, plan = await _study_with_plan(session, admin_user, analysis_run)
    assert await build_level3_inputs(session, study, plan) is None


@pytest.mark.asyncio
async def test_extracting_persists_level3_bundle_and_routes_to_reproducing(
    session, admin_user, analysis_run, de_template, monkeypatch
):
    """Regression: `_handle_extracting` must PERSIST the level3 bundle it builds, not just set it in
    memory. `build_level3_inputs` issues SELECTs whose autoflush flushes `evidence_json` and clears its
    dirty flag; a following in-place `evidence["level3"] = ...` on the plain (non-Mutable) JSONB column
    plus a same-reference reassignment was then not tracked, so the study reached `reproducing` with NO
    level3 persisted. The next tick's `_handle_reproducing` loaded evidence without level3, fell through
    its `if not level3` guard to `comparing`, and the whole Level-3 finding silently collapsed to a
    Level-2 verdict. This asserts the DB-persisted evidence, so it fails on the in-place-mutation bug."""

    async def _no_dashboard(*a, **k):
        return None

    # Isolate the persistence behavior from QC-dashboard generation (a bare run has no MultiQC data).
    monkeypatch.setattr(QCDashboardService, "get_dashboard_by_run", _no_dashboard)
    monkeypatch.setattr(QCDashboardService, "generate_qc_dashboard", _no_dashboard)

    await _count_matrix_file(session, admin_user, analysis_run)
    study, plan = await _study_with_plan(session, admin_user, analysis_run)  # real level3 inputs
    study.state = "extracting"
    await session.commit()

    await ValidationDriverService._handle_extracting(session, study)
    await session.commit()

    # Read back the committed value (not the in-memory dict, which holds level3 even when the bug hides it).
    await session.refresh(study)
    assert study.state == "reproducing"
    evidence = study.evidence_json or {}
    assert evidence.get("level3") is not None, "evidence['level3'] was set in memory but not persisted"
    assert evidence["level3"]["template_id"] == de_template.id
    assert evidence["level3"]["kind"] == "gene"
