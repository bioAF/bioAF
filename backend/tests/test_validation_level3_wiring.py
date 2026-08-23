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
from app.services.validation_level3_service import build_level3_inputs, resolve_level3
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


async def _study_with_plan(session, admin_user, run, *, design=_DESIGN, claim=_CLAIM, pipeline_key="nf-core/rnaseq"):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    study.analysis_run_id = run.id
    await session.flush()
    plan = await ReproductionPlanService.create_plan(
        session, study, admin_user.id, pipeline_key=pipeline_key, differential_design=design
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


_PAIRED_DESIGN = {
    "contrasts": [
        {
            "name": "mucoderm vs tcs",
            "test_samples": ["SRX1", "SRX2"],
            "reference_samples": ["SRX3", "SRX4"],
            "subjects": {"SRX1": "donorA", "SRX2": "donorB", "SRX3": "donorA", "SRX4": "donorB"},
        }
    ],
    "thresholds": {"log2fc": 1.0, "padj": 0.05},
}


@pytest.mark.asyncio
async def test_build_level3_inputs_emits_block_labels_for_paired_design(session, admin_user, analysis_run, de_template):
    await _count_matrix_file(session, admin_user, analysis_run)
    study, plan = await _study_with_plan(session, admin_user, analysis_run, design=_PAIRED_DESIGN)

    level3 = await build_level3_inputs(session, study, plan)

    assert level3 is not None
    params = level3["parameters"]
    # block labels are aligned to the notebook's sample order: test_samples then reference_samples.
    assert params["test_samples"] == "SRX1,SRX2"
    assert params["reference_samples"] == "SRX3,SRX4"
    assert params["block_labels"] == "donorA,donorB,donorA,donorB"


@pytest.mark.asyncio
async def test_build_level3_inputs_omits_block_labels_for_unpaired_design(
    session, admin_user, analysis_run, de_template
):
    await _count_matrix_file(session, admin_user, analysis_run)
    study, plan = await _study_with_plan(session, admin_user, analysis_run)  # default _DESIGN, no subjects
    level3 = await build_level3_inputs(session, study, plan)
    assert level3 is not None
    assert "block_labels" not in level3["parameters"]


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
        session,
        admin_user,
        analysis_run,
        design=_INTERVAL_DESIGN,
        claim=_INTERVAL_CLAIM,
        pipeline_key="nf-core/atacseq",
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
        session,
        admin_user,
        analysis_run,
        design=_INTERVAL_DESIGN,
        claim=_INTERVAL_CLAIM,
        pipeline_key="nf-core/atacseq",
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


_UNDERPOWERED_DESIGN = {
    "contrasts": [
        {
            "name": "dex vs untreated",
            "test_samples": ["SRX1"],
            "reference_samples": ["SRX3", "SRX4"],
        }
    ],
    "thresholds": {"log2fc": 1.0, "padj": 0.05},
}


@pytest.mark.asyncio
async def test_build_level3_inputs_refuses_a_contrast_deseq2_cannot_fit(session, admin_user, analysis_run, de_template):
    """The C1 gate's replicate guard is bypassed twice: `create_plan` writes the LLM's draft design
    straight to the plan, and `_resolve_sample_design` rewrites the arms AFTER the fetch (dropping
    picks that were never fetched, so a 3-vs-3 ratified at C1 becomes 1-vs-3). The point-of-use check
    is what actually protects the run: refuse here, degrade to Level-2, spend no compute."""
    await _count_matrix_file(session, admin_user, analysis_run)
    study, plan = await _study_with_plan(session, admin_user, analysis_run, design=_UNDERPOWERED_DESIGN)
    assert await build_level3_inputs(session, study, plan) is None


@pytest.mark.asyncio
async def test_build_level3_inputs_accepts_two_samples_per_arm(session, admin_user, analysis_run, de_template):
    """Two per arm is valid for DESeq2 (underpowered, but a real design a small lab runs)."""
    await _count_matrix_file(session, admin_user, analysis_run)
    study, plan = await _study_with_plan(session, admin_user, analysis_run)  # _DESIGN is 2 vs 2
    level3 = await build_level3_inputs(session, study, plan)
    assert level3 is not None
    assert level3["parameters"]["test_samples"] == "SRX1,SRX2"


# --- (pipeline, kind) wiring: deterministic selection, refusal, multi-file ---


@pytest_asyncio.fixture
async def chip_run(session, admin_user):
    run = PipelineRun(
        organization_id=admin_user.organization_id,
        submitted_by_user_id=admin_user.id,
        pipeline_name="nf-core/chipseq",
        pipeline_version="2.1.0",
        status="completed",
    )
    session.add(run)
    await session.flush()
    return run


async def _file_at(session, admin_user, run, uri, filename):
    """A run output at a real published PATH, which is what disambiguates same-named matrices."""
    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri=uri,
        storage_uri=uri,
        filename=filename,
        file_type="count_matrix",
        source_pipeline_run_id=run.id,
    )
    session.add(f)
    await session.flush()
    return f


_SALMON_MATRIX = "salmon.merged.gene_counts.tsv"


@pytest.mark.asyncio
async def test_chipseq_resolves_the_consensus_peak_matrix(session, admin_user, chip_run, da_template):
    """chipseq and atacseq share the consensus-peak matrix and the DA template."""
    f = await _count_matrix_file(session, admin_user, chip_run, filename=_NFCORE_CONSENSUS)
    study, plan = await _study_with_plan(
        session,
        admin_user,
        chip_run,
        design=_INTERVAL_DESIGN,
        claim=_INTERVAL_CLAIM,
        pipeline_key="nf-core/chipseq",
    )
    level3 = await build_level3_inputs(session, study, plan)
    assert level3 is not None
    assert level3["template_id"] == da_template.id
    assert level3["input_file_ids"] == [f.id]


@pytest.mark.asyncio
async def test_scrnaseq_does_not_resolve_as_rnaseq(session, admin_user, analysis_run, de_template):
    """`scrnaseq` CONTAINS `rnaseq`. A substring rule would hand an scRNA-seq study the bulk gene-count
    wiring and run DESeq2 on a matrix that is not there. Keys match exactly or not at all."""
    from app.services.validation_level3_service import _WIRING

    assert (
        _WIRING[("nf-core/scrnaseq", "gene")].template_notebook_path
        != _WIRING[("nf-core/rnaseq", "gene")].template_notebook_path
    )
    # A salmon gene-count matrix in an scRNA-seq run must not be picked up by the scrnaseq route.
    await _count_matrix_file(session, admin_user, analysis_run)
    study, plan = await _study_with_plan(session, admin_user, analysis_run, pipeline_key="nf-core/scrnaseq")
    decision = await resolve_level3(session, study, plan)
    assert decision.inputs is None
    assert decision.reason_code == "no_input_file"
    assert decision.reason is not None
    assert "scrnaseq" in decision.reason


@pytest.mark.asyncio
async def test_rnaseq_prefers_the_aligner_matrix_over_the_pseudoaligner_matrix(
    session, admin_user, analysis_run, de_template
):
    """bioAF runs nf-core/rnaseq with `aligner: star_salmon` AND `pseudo_aligner: salmon`, so BOTH
    `star_salmon/` and `salmon/` publish a file called `salmon.merged.gene_counts.tsv` with different
    column bases. Matching on the basename alone returned whichever row the database happened to
    yield. The declared aligner's quantification wins, deterministically."""
    pseudo = await _file_at(session, admin_user, analysis_run, f"gs://b/run/salmon/{_SALMON_MATRIX}", _SALMON_MATRIX)
    aligner = await _file_at(
        session, admin_user, analysis_run, f"gs://b/run/star_salmon/{_SALMON_MATRIX}", _SALMON_MATRIX
    )
    study, plan = await _study_with_plan(session, admin_user, analysis_run)

    level3 = await build_level3_inputs(session, study, plan)

    assert level3 is not None
    assert level3["input_file_ids"] == [aligner.id]
    assert pseudo.id not in level3["input_file_ids"]


@pytest.mark.asyncio
async def test_file_selection_is_deterministic_across_repeated_calls(session, admin_user, analysis_run, de_template):
    await _file_at(session, admin_user, analysis_run, f"gs://b/run/salmon/{_SALMON_MATRIX}", _SALMON_MATRIX)
    await _file_at(session, admin_user, analysis_run, f"gs://b/run/star_salmon/{_SALMON_MATRIX}", _SALMON_MATRIX)
    study, plan = await _study_with_plan(session, admin_user, analysis_run)
    picks = set()
    for _ in range(5):
        level3 = await build_level3_inputs(session, study, plan)
        assert level3 is not None
        picks.add(tuple(level3["input_file_ids"]))
    assert len(picks) == 1


@pytest.mark.asyncio
async def test_two_indistinguishable_candidates_refuse_rather_than_pick(session, admin_user, analysis_run, de_template):
    """This is a screening tool for papers of unknown validity. Two candidate matrices that no rule
    separates is an unanswerable question, and a stated refusal beats an unexplained pick."""
    await _file_at(session, admin_user, analysis_run, f"gs://b/run/a/{_SALMON_MATRIX}", _SALMON_MATRIX)
    await _file_at(session, admin_user, analysis_run, f"gs://b/run/b/{_SALMON_MATRIX}", _SALMON_MATRIX)
    study, plan = await _study_with_plan(session, admin_user, analysis_run)

    decision = await resolve_level3(session, study, plan)

    assert decision.inputs is None
    assert decision.reason_code == "ambiguous_input_file"
    assert decision.reason is not None
    assert _SALMON_MATRIX in decision.reason


@pytest.mark.asyncio
async def test_atacseq_prefers_the_merged_library_consensus_matrix(session, admin_user, analysis_run, da_template):
    """nf-core/atacseq publishes a per-library (mLb) and a per-replicate (mRp) consensus matrix, and
    they have different column bases. Our design's arms name libraries, so mLb is the right one."""
    mrp = await _count_matrix_file(
        session, admin_user, analysis_run, filename="consensus_peaks.mRp.clN.featureCounts.txt"
    )
    mlb = await _count_matrix_file(session, admin_user, analysis_run, filename=_NFCORE_CONSENSUS)
    study, plan = await _study_with_plan(
        session,
        admin_user,
        analysis_run,
        design=_INTERVAL_DESIGN,
        claim=_INTERVAL_CLAIM,
        pipeline_key="nf-core/atacseq",
    )

    level3 = await build_level3_inputs(session, study, plan)

    assert level3 is not None
    assert level3["input_file_ids"] == [mlb.id]
    assert mrp.id not in level3["input_file_ids"]


@pytest.mark.asyncio
async def test_atacseq_falls_back_to_the_replicate_consensus_when_it_is_the_only_one(
    session, admin_user, analysis_run, da_template
):
    f = await _count_matrix_file(
        session, admin_user, analysis_run, filename="consensus_peaks.mRp.clN.featureCounts.txt"
    )
    study, plan = await _study_with_plan(
        session,
        admin_user,
        analysis_run,
        design=_INTERVAL_DESIGN,
        claim=_INTERVAL_CLAIM,
        pipeline_key="nf-core/atacseq",
    )
    level3 = await build_level3_inputs(session, study, plan)
    assert level3 is not None
    assert level3["input_file_ids"] == [f.id]


@pytest.mark.asyncio
async def test_the_selected_input_is_recorded_for_audit(session, admin_user, analysis_run, de_template):
    """Which matrix a reproduction ran on is part of its provenance: a verdict that cannot say which
    file produced it cannot be re-baselined or challenged."""
    await _count_matrix_file(session, admin_user, analysis_run)
    study, plan = await _study_with_plan(session, admin_user, analysis_run)
    level3 = await build_level3_inputs(session, study, plan)
    assert level3 is not None
    assert level3["input_files"] == [_SALMON_MATRIX]


# Every decline path names itself, so an `inconclusive` study can say what stopped Level-3 instead of
# leaving the answer in a server log.
@pytest.mark.asyncio
async def test_decline_reasons_are_distinguishable(session, admin_user, analysis_run, de_template):
    seen = {}
    reasons: list[str] = []

    async def _code(**kwargs):
        study, plan = await _study_with_plan(session, admin_user, analysis_run, **kwargs)
        return study, plan

    # no plan
    study, _ = await _code()
    d = await resolve_level3(session, study, None)
    seen["no_plan"] = d.reason_code
    reasons.append(d.reason)

    # unconfirmed claim
    study, plan = await _code(claim={**_CLAIM, "confirmed": False})
    decision = await resolve_level3(session, study, plan)
    seen["no_finding_claim"] = decision.reason_code
    reasons.append(decision.reason)

    # no contrast
    study, plan = await _code(design={"contrasts": [], "thresholds": {}})
    decision = await resolve_level3(session, study, plan)
    seen["no_contrast"] = decision.reason_code
    reasons.append(decision.reason)

    # too few replicates
    study, plan = await _code(design=_UNDERPOWERED_DESIGN)
    decision = await resolve_level3(session, study, plan)
    seen["too_few_replicates"] = decision.reason_code
    reasons.append(decision.reason)

    # no analysis run
    study, plan = await _code()
    study.analysis_run_id = None
    await session.flush()
    decision = await resolve_level3(session, study, plan)
    seen["no_analysis_run"] = decision.reason_code
    reasons.append(decision.reason)

    # no wiring for this (pipeline, kind)
    study, plan = await _code(pipeline_key="nf-core/fetchngs")
    seen["no_wiring"] = (await resolve_level3(session, study, plan)).reason_code
    reasons.append((await resolve_level3(session, study, plan)).reason)

    assert seen == {k: k for k in seen}
    assert all(reasons), "every decline path must carry a human-readable reason"
    assert len(set(reasons)) == len(reasons), "two decline paths share the same wording"


@pytest.mark.asyncio
async def test_missing_input_file_and_missing_template_read_differently(session, admin_user, analysis_run):
    """A missing input file is a fact about the paper's run; a missing template is a fact about this
    bioAF instance. Collapsing them tells a scientist to go fix the wrong thing."""
    study, plan = await _study_with_plan(session, admin_user, analysis_run)
    no_file = await resolve_level3(session, study, plan)
    assert no_file.reason_code == "no_input_file"

    await _count_matrix_file(session, admin_user, analysis_run)
    no_template = await resolve_level3(session, study, plan)
    assert no_template.reason_code == "no_template"
    assert no_file.reason != no_template.reason


# --- nf-core/scrnaseq: the pseudobulk route (N per-sample h5ad files, one transform) ---


@pytest_asyncio.fixture
async def scrna_run(session, admin_user):
    run = PipelineRun(
        organization_id=admin_user.organization_id,
        submitted_by_user_id=admin_user.id,
        pipeline_name="nf-core/scrnaseq",
        pipeline_version="2.7.1",
        status="completed",
    )
    session.add(run)
    await session.flush()
    return run


@pytest_asyncio.fixture
async def pseudobulk_template(session, admin_user):
    tmpl = TemplateNotebook(
        organization_id=admin_user.organization_id,
        name="Differential Expression (pseudobulk DESeq2, headless)",
        category="differential_expression",
        notebook_path="notebooks/de_pseudobulk_deseq2.ipynb",
        parameters_json={},
        compatible_with="nf-core/scrnaseq",
        is_builtin=True,
    )
    session.add(tmpl)
    await session.flush()
    return tmpl


async def _h5ads(session, admin_user, run, names):
    return [await _file_at(session, admin_user, run, f"gs://b/run/{n}", n) for n in names]


async def _scrna_study(session, admin_user, run):
    return await _study_with_plan(session, admin_user, run, pipeline_key="nf-core/scrnaseq")


@pytest.mark.asyncio
async def test_scrnaseq_resolves_every_per_sample_filtered_matrix(session, admin_user, scrna_run, pseudobulk_template):
    """nf-core/scrnaseq emits one h5ad per sample, and pseudobulk needs all of them: each file becomes
    one column of the genes x samples matrix DESeq2 consumes."""
    files = await _h5ads(session, admin_user, scrna_run, ["SRX1_filtered_matrix.h5ad", "SRX2_filtered_matrix.h5ad"])
    study, plan = await _scrna_study(session, admin_user, scrna_run)

    level3 = await build_level3_inputs(session, study, plan)

    assert level3 is not None
    assert level3["template_id"] == pseudobulk_template.id
    assert level3["kind"] == "gene"
    assert level3["transform"] == "pseudobulk"
    assert set(level3["input_file_ids"]) == {f.id for f in files}
    paths = level3["parameters"]["counts_paths"].split(",")
    assert len(paths) == 2
    assert all(p.endswith("_filtered_matrix.h5ad") for p in paths)
    # The single-file parameter must not be set: the pseudobulk template reads counts_paths.
    assert "counts_path" not in level3["parameters"]


@pytest.mark.asyncio
async def test_scrnaseq_excludes_the_concatenated_matrix(session, admin_user, scrna_run, pseudobulk_template):
    """`concat_h5ad.py` calls `ad.concat(..., label="sample")`, and that label argument OVERWRITES the
    clean obs["sample"] each per-sample file already carried with the file path stem, so the combined
    file's labels carry an `_filtered` suffix. Using it would need string surgery against an upstream
    convention that can drift; the per-sample files need no grouping at all."""
    await _h5ads(
        session,
        admin_user,
        scrna_run,
        ["SRX1_filtered_matrix.h5ad", "SRX2_filtered_matrix.h5ad", "combined_filtered_matrix.h5ad"],
    )
    study, plan = await _scrna_study(session, admin_user, scrna_run)

    level3 = await build_level3_inputs(session, study, plan)

    assert level3 is not None
    assert level3["input_files"] == ["SRX1_filtered_matrix.h5ad", "SRX2_filtered_matrix.h5ad"]


@pytest.mark.asyncio
async def test_scrnaseq_refuses_a_run_with_only_raw_matrices(session, admin_user, scrna_run, pseudobulk_template):
    """`raw` holds every barcode the sequencer saw, overwhelmingly empty droplets carrying ambient RNA.
    Summing it would pseudobulk the ambient soup along with the cells. Refuse; never fall back."""
    await _h5ads(session, admin_user, scrna_run, ["SRX1_raw_matrix.h5ad", "SRX2_raw_matrix.h5ad"])
    study, plan = await _scrna_study(session, admin_user, scrna_run)

    decision = await resolve_level3(session, study, plan)

    assert decision.inputs is None
    assert decision.reason_code == "no_input_file"


@pytest.mark.asyncio
async def test_scrnaseq_never_mixes_raw_into_the_filtered_set(session, admin_user, scrna_run, pseudobulk_template):
    await _h5ads(
        session,
        admin_user,
        scrna_run,
        ["SRX1_filtered_matrix.h5ad", "SRX1_raw_matrix.h5ad", "SRX2_filtered_matrix.h5ad", "SRX2_raw_matrix.h5ad"],
    )
    study, plan = await _scrna_study(session, admin_user, scrna_run)
    level3 = await build_level3_inputs(session, study, plan)
    assert level3 is not None
    assert level3["input_files"] == ["SRX1_filtered_matrix.h5ad", "SRX2_filtered_matrix.h5ad"]


@pytest.mark.asyncio
async def test_scrnaseq_prefers_cellbender_over_plain_filtered(session, admin_user, scrna_run, pseudobulk_template):
    """When CellBender ran, its output is the same cell-called matrix with ambient RNA removed, so it
    is the better pseudobulk input. Plain `filtered` is the fallback."""
    await _h5ads(
        session,
        admin_user,
        scrna_run,
        [
            "SRX1_filtered_matrix.h5ad",
            "SRX2_filtered_matrix.h5ad",
            "SRX1_cellbender_filter_matrix.h5ad",
            "SRX2_cellbender_filter_matrix.h5ad",
        ],
    )
    study, plan = await _scrna_study(session, admin_user, scrna_run)

    level3 = await build_level3_inputs(session, study, plan)

    assert level3 is not None
    assert level3["input_files"] == [
        "SRX1_cellbender_filter_matrix.h5ad",
        "SRX2_cellbender_filter_matrix.h5ad",
    ]


@pytest.mark.asyncio
async def test_scrnaseq_supported_kinds_are_declared():
    from app.services.validation_level3_service import supported_finding_kinds

    assert supported_finding_kinds("nf-core/scrnaseq") == ["gene"]
    assert supported_finding_kinds("nf-core/rnaseq") == ["gene"]
    assert supported_finding_kinds("nf-core/chipseq") == ["interval"]
    assert supported_finding_kinds("nf-core/atacseq") == ["interval"]
    assert supported_finding_kinds("nf-core/fetchngs") == []
    assert supported_finding_kinds(None) == []
