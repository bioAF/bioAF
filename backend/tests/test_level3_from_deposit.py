"""plan_7 step 8: reproduce the finding from the deposited matrix.

`_handle_reproducing` reads `evidence["level3"]`, runs the named template against the named
`input_file_ids`, and scores concordance. It does not know or care that those file ids came from a
pipeline run. This step is where that becomes true rather than nearly true: a second constructor
builds the SAME bundle shape out of a deposit.

The template is chosen by what step 6 MEASURED the values to be, not by the filename and not by the
pipeline. That matters because all three existing headless templates are DESeq2, which requires
integer counts, and the bioinformaticians said plainly that a TPM table is a thing we will be
handed. GSE274331's is one: 37,248 rows, six samples, every column summing to exactly 1e6.
"""

import pytest
import pytest_asyncio

from app.models.file import File
from app.models.template_notebook import TemplateNotebook
from app.models.validation_study import ValidationStudy
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_level3_service import resolve_level3_from_deposit, template_for_value_type

_DESIGN = {
    "contrasts": [
        {
            "name": "KD vs control",
            "test_condition": "H2AS40-KD",
            "reference_condition": "Control-KD",
            "test_samples": ["H2AS40-KD_1", "H2AS40-KD_2", "H2AS40-KD_3"],
            "reference_samples": ["Control-KD_1", "Control-KD_2", "Control-KD_3"],
        }
    ],
    "thresholds": {"log2fc": 1.0, "padj": 0.05},
}

_CLAIM = {
    "kind": "gene",
    "namespace": "ensembl_gene",
    "confirmed": True,
    "finding_set": {"kind": "gene", "namespace": "ensembl_gene", "entities": [], "n_sig": 0},
}


# ---- which test to run ----


def test_integer_counts_use_deseq2():
    t = template_for_value_type("counts", kind="gene")
    assert t.template_notebook_path == "notebooks/de_bulk_deseq2.ipynb"
    assert t.method == "deseq2"


def test_interval_counts_use_the_peaks_template():
    t = template_for_value_type("counts", kind="interval")
    assert t.template_notebook_path == "notebooks/da_peaks_deseq2.ipynb"


@pytest.mark.parametrize("value_type", ["tpm_or_cpm", "tpm", "cpm", "normalized_other", "log_transformed"])
def test_a_normalized_matrix_never_reaches_deseq2(value_type):
    """The load-bearing assertion of step 8. DESeq2 requires integer counts and estimates its own
    size factors; handing it TPM invalidates the dispersion model and yields numbers that are
    confidently wrong rather than obviously wrong."""
    t = template_for_value_type(value_type, kind="gene")
    assert t.template_notebook_path == "notebooks/de_normalized_limma.ipynb"
    assert t.method == "limma_trend"


def test_an_unknown_value_type_is_refused_rather_than_guessed():
    """Defaulting to counts would be the same defect as trusting the filename."""
    assert template_for_value_type("unknown", kind="gene") is None


# ---- the bundle ----


@pytest_asyncio.fixture
async def limma_template(session, admin_user):
    tmpl = TemplateNotebook(
        organization_id=admin_user.organization_id,
        name="Differential Expression (limma-trend, headless)",
        category="differential_expression",
        notebook_path="notebooks/de_normalized_limma.ipynb",
        parameters_json={"id_column": ""},
        is_builtin=True,
    )
    session.add(tmpl)
    await session.flush()
    return tmpl


@pytest_asyncio.fixture
async def deposited_file(session, admin_user):
    f = File(
        organization_id=admin_user.organization_id,
        filename="GSE274331_TPMs.xlsx",
        storage_uri="s3://x/m.tsv",
        file_type="table",
        source_type="external_deposit",
        artifact_type="deposited_matrix",
        uploader_user_id=admin_user.id,
    )
    session.add(f)
    await session.flush()
    return f


@pytest_asyncio.fixture
async def deposit_study(session, admin_user, limma_template, deposited_file):
    study = ValidationStudy(
        organization_id=admin_user.organization_id,
        requested_by_user_id=admin_user.id,
        source_accession="GSE274331",
        state="reproducing",
        evidence_json={
            "route": "deposit",
            "deposit": {
                "files": [
                    {
                        "file_id": deposited_file.id,
                        "filename": "GSE274331_TPMs.xlsx",
                        "storage_uri": "s3://x/m.tsv",
                        "artifact_type": "deposited_matrix",
                    }
                ]
            },
            "deposit_inspection": {
                "value_type_observed": "tpm_or_cpm",
                "id_column": "",
                "columns": [
                    "Control-KD_1",
                    "Control-KD_2",
                    "Control-KD_3",
                    "H2AS40-KD_1",
                    "H2AS40-KD_2",
                    "H2AS40-KD_3",
                ],
            },
        },
    )
    session.add(study)
    await session.flush()
    plan = await ReproductionPlanService.create_plan(
        session, study, admin_user.id, pipeline_key="nf-core/rnaseq", differential_design=_DESIGN
    )
    plan.finding_claim_json = _CLAIM
    await session.flush()
    return study


@pytest.mark.asyncio
async def test_the_deposit_bundle_has_the_same_shape_as_the_pipeline_one(session, deposit_study, admin_user):
    """Asserted on KEYS so the two constructors cannot drift. `_handle_reproducing` consumes this
    and is not modified by plan_7; if the shapes diverge, it breaks silently."""
    plan = await ReproductionPlanService.get_plan(session, deposit_study.id, admin_user.organization_id)
    decision = await resolve_level3_from_deposit(session, deposit_study, plan)
    assert decision.inputs is not None
    assert set(decision.inputs) >= {
        "template_id",
        "parameters",
        "input_file_ids",
        "input_files",
        "paper_finding_set",
        "kind",
        "contrast",
    }


@pytest.mark.asyncio
async def test_the_bundle_names_the_deposited_file_and_the_method(session, deposit_study, admin_user):
    plan = await ReproductionPlanService.get_plan(session, deposit_study.id, admin_user.organization_id)
    decision = await resolve_level3_from_deposit(session, deposit_study, plan)
    assert decision.inputs["input_file_ids"] == [deposit_study.evidence_json["deposit"]["files"][0]["file_id"]]
    assert decision.inputs["input_files"] == ["GSE274331_TPMs.xlsx"]
    # Recorded so a divergence can be attributed to the METHOD: a limma-trend result compared against
    # a paper's DESeq2 result is a named difference, not an unexplained one.
    assert decision.inputs["method"] == "limma_trend"


@pytest.mark.asyncio
async def test_the_arms_reach_the_template_as_matrix_columns(session, deposit_study, admin_user):
    plan = await ReproductionPlanService.get_plan(session, deposit_study.id, admin_user.organization_id)
    decision = await resolve_level3_from_deposit(session, deposit_study, plan)
    p = decision.inputs["parameters"]
    assert p["test_samples"] == "H2AS40-KD_1,H2AS40-KD_2,H2AS40-KD_3"
    assert p["reference_samples"] == "Control-KD_1,Control-KD_2,Control-KD_3"
    assert p["lfc_threshold"] == 1.0
    assert p["padj_threshold"] == 0.05


@pytest.mark.asyncio
async def test_the_matrix_id_column_is_passed_through(session, deposit_study, admin_user):
    """A deposit's id column is whatever the depositor wrote, including empty. The wiring's fixed
    `id_column` is a property of an nf-core output and cannot speak for a deposit."""
    plan = await ReproductionPlanService.get_plan(session, deposit_study.id, admin_user.organization_id)
    decision = await resolve_level3_from_deposit(session, deposit_study, plan)
    assert decision.inputs["parameters"]["id_column"] == ""


@pytest.mark.asyncio
async def test_no_deposit_declines_with_a_reason(session, admin_user):
    study = ValidationStudy(
        organization_id=admin_user.organization_id,
        requested_by_user_id=admin_user.id,
        state="reproducing",
        evidence_json={"route": "deposit"},
    )
    session.add(study)
    await session.flush()
    plan = await ReproductionPlanService.create_plan(
        session, study, admin_user.id, pipeline_key="nf-core/rnaseq", differential_design=_DESIGN
    )
    decision = await resolve_level3_from_deposit(session, study, plan)
    assert decision.inputs is None
    assert decision.reason_code
    assert decision.reason


@pytest.mark.asyncio
async def test_an_unmeasured_value_type_declines_rather_than_running_the_wrong_test(session, deposit_study, admin_user):
    ev = dict(deposit_study.evidence_json)
    ev["deposit_inspection"] = {**ev["deposit_inspection"], "value_type_observed": "unknown"}
    deposit_study.evidence_json = ev
    await session.flush()
    plan = await ReproductionPlanService.get_plan(session, deposit_study.id, admin_user.organization_id)
    decision = await resolve_level3_from_deposit(session, deposit_study, plan)
    assert decision.inputs is None
    assert decision.reason_code == "unknown_value_type"


@pytest.mark.asyncio
async def test_no_confirmed_finding_claim_declines(session, deposit_study, admin_user):
    plan = await ReproductionPlanService.get_plan(session, deposit_study.id, admin_user.organization_id)
    plan.finding_claim_json = None
    await session.flush()
    decision = await resolve_level3_from_deposit(session, deposit_study, plan)
    assert decision.inputs is None
    assert decision.reason_code == "no_finding_claim"


# ---- the driver builds the bundle on the deposit route ----


@pytest.mark.asyncio
async def test_the_inspection_step_builds_the_level3_bundle(session, admin_user, limma_template, deposited_file):
    """The two routes converge at `reproducing`, which reads evidence["level3"]. On the pipeline
    route `_handle_extracting` builds it; on the deposit route the inspection step does, and
    `_handle_reproducing` is untouched by plan_7."""
    from app.services.validation_driver_service import ValidationDriverService

    matrix = (
        "\tControl-KD_1\tControl-KD_2\tControl-KD_3\tH2AS40-KD_1\tH2AS40-KD_2\tH2AS40-KD_3\n"
        "ENSG1\t400000.0\t400000.0\t400000.0\t300000.0\t300000.0\t300000.0\n"
        "ENSG2\t600000.0\t600000.0\t600000.0\t700000.0\t700000.0\t700000.0\n"
    )

    class _S:
        async def read_text(self, uri, *, encoding="utf-8"):
            return matrix

    study = ValidationStudy(
        organization_id=admin_user.organization_id,
        requested_by_user_id=admin_user.id,
        source_accession="GSE274331",
        state="inspecting_deposit",
        evidence_json={
            "route": "deposit",
            "deposit_selection": {"primary_matrix": "m.tsv", "matrix_files": ["m.tsv"], "value_type": "tpm"},
            "deposit": {
                "files": [
                    {
                        "file_id": deposited_file.id,
                        "filename": "GSE274331_TPMs.xlsx",
                        "storage_uri": "s3://x/m.tsv",
                        "artifact_type": "deposited_matrix",
                    }
                ]
            },
        },
    )
    session.add(study)
    await session.flush()
    plan = await ReproductionPlanService.create_plan(
        session, study, admin_user.id, pipeline_key="nf-core/rnaseq", differential_design=_DESIGN
    )
    plan.finding_claim_json = _CLAIM
    await session.flush()

    await ValidationDriverService._handle_inspecting_deposit(session, study, storage_adapter=_S())

    assert study.state == "reproducing", study.evidence_json.get("deposit_failed")
    assert "level3" in study.evidence_json, study.evidence_json.get("level3_skipped")
    level3 = study.evidence_json["level3"]
    assert level3["method"] == "limma_trend"
    assert level3["source"] == "deposit"
    assert level3["template_id"] == limma_template.id
