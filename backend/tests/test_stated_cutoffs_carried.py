"""change_7.5 section 1.2: the stated kind and operator reach everything that exists.

Study 38's `P < 0.01` survived extraction as a cutoff and then met code that could not carry it: the
templates wrote no raw P value, the analysis refused a P value outright, no caller told the
normalizers which measure to read, the filter hard-coded `<=` and `>=`, a linear fold change was
measured as log2, the gate's design edit dropped the cutoffs, and the report printed the legacy pair.

Each test here runs the real code with no model call.
"""

import json
import re
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.models.file import File
from app.models.template_notebook import TemplateNotebook
from app.models.validation_study import ValidationStudy
from app.services.notebook_execution_service import NotebookExecutionService
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.result_set_normalizer import FindingEntity, FindingSet, normalize_gene_table, normalize_interval_table
from app.services.template_notebook_service import PACKAGE_TEMPLATES_DIR
from app.services.validation_claim_cutoffs import analysis_cutoffs, contrast_cutoff_words
from app.services.validation_driver_service import ValidationDriverService
from app.services.validation_level3_service import resolve_level3_from_deposit
from app.services.validation_report_summary import summarize
from app.services.validation_study_service import ValidationStudyService

_P = {"kind": "pvalue", "operator": "<", "value": 0.01}


# ---- the templates write a raw P value ----


@pytest.mark.parametrize(
    "notebook, source_column",
    [
        ("de_bulk_deseq2.ipynb", "res$pvalue"),
        ("da_peaks_deseq2.ipynb", "res$pvalue"),
        ("de_pseudobulk_deseq2.ipynb", "res$pvalue"),
        ("de_normalized_limma.ipynb", "res$P.Value"),
    ],
)
def test_each_template_writes_the_raw_p_value_beside_the_adjusted_one(notebook, source_column):
    nb = json.loads((PACKAGE_TEMPLATES_DIR / notebook).read_text())
    source = "\n".join("".join(c.get("source", [])) for c in nb["cells"])
    written = re.search(r"out <- data\.frame\((.*?)\)\n", source, re.S).group(1)
    assert f"pvalue = {source_column}" in written
    assert "padj = " in written


# ---- the analysis cutoffs carry kind and operator ----


def test_a_p_value_claim_is_executable_with_its_kind_and_operator():
    out = analysis_cutoffs({"cutoffs": [_P]}, {})
    assert out["refusal"] is None
    assert out["significance"] == _P
    assert out["effect"] is None
    assert out["statement"] == "P < 0.01, no fold-change requirement"


def test_the_stated_operators_are_carried():
    out = analysis_cutoffs(
        {
            "cutoffs": [
                {"kind": "padj", "operator": "<=", "value": 0.05},
                {"kind": "abs_log2fc", "operator": ">", "value": 1},
            ]
        },
        {},
    )
    assert out["significance"] == {"kind": "padj", "operator": "<=", "value": 0.05}
    assert out["effect"] == {"kind": "abs_log2fc", "operator": ">", "value": 1.0}


def test_a_linear_fold_change_keeps_its_operator_on_the_log2_scale():
    out = analysis_cutoffs(
        {
            "cutoffs": [
                {"kind": "padj", "operator": "<", "value": 0.05},
                {"kind": "fold_change", "operator": ">=", "value": 2},
            ]
        },
        {},
    )
    assert out["effect"] == {"kind": "abs_log2fc", "operator": ">=", "value": 1.0}


# ---- the filter honours the stated operators ----

_EDGE = "gene,log2FoldChange,pvalue,padj\nAT,1.0,0.01,0.01\nIN,2.0,0.001,0.001\n"


def test_less_than_excludes_a_value_on_the_cutoff_and_at_most_includes_it():
    strict = normalize_gene_table(
        _EDGE, lfc_threshold=0.0, padj_threshold=0.01, significance_kind="padj", significance_operator="<"
    )
    inclusive = normalize_gene_table(
        _EDGE, lfc_threshold=0.0, padj_threshold=0.01, significance_kind="padj", significance_operator="<="
    )
    assert {e.id for e in strict.entities} == {"IN"}
    assert {e.id for e in inclusive.entities} == {"AT", "IN"}


def test_greater_than_excludes_an_effect_on_the_cutoff_and_at_least_includes_it():
    strict = normalize_gene_table(
        _EDGE, lfc_threshold=1.0, padj_threshold=0.05, significance_kind="padj", effect_operator=">"
    )
    inclusive = normalize_gene_table(
        _EDGE, lfc_threshold=1.0, padj_threshold=0.05, significance_kind="padj", effect_operator=">="
    )
    assert {e.id for e in strict.entities} == {"IN"}
    assert {e.id for e in inclusive.entities} == {"AT", "IN"}


def test_the_interval_filter_honours_the_operators_too():
    table = "chr,start,end,log2FoldChange,pvalue\nchr1,1,100,1.0,0.01\nchr2,1,100,2.0,0.001\n"
    fs = normalize_interval_table(
        table,
        lfc_threshold=1.0,
        padj_threshold=0.01,
        significance_kind="pvalue",
        significance_operator="<",
        effect_operator=">",
    )
    assert {e.id for e in fs.entities} == {"chr2:1-100"}


def test_a_p_value_cutoff_reads_the_p_value_column_when_both_are_present():
    table = "gene,log2FoldChange,pvalue,padj\nA,2.0,0.005,0.2\nB,2.0,0.02,0.001\n"
    fs = normalize_gene_table(
        table, lfc_threshold=0.0, padj_threshold=0.01, significance_kind="pvalue", significance_operator="<"
    )
    assert {e.id for e in fs.entities} == {"A"}


# ---- the level 3 bundle carries the stated definition ----

_P_DESIGN = {
    "selected_contrast": {"contrast_index": 0, "decided_by": "only_contrast"},
    "contrasts": [
        {
            "name": "KO vs WT",
            "test_condition": "KO",
            "reference_condition": "WT",
            "test_samples": ["KO_1", "KO_2"],
            "reference_samples": ["WT_1", "WT_2"],
            "cutoffs": [_P],
            "thresholds": {"padj": None, "log2fc": None},
        }
    ],
    "thresholds": {"log2fc": None, "padj": None},
}


@pytest_asyncio.fixture
async def p_value_deposit_study(session, admin_user):
    tmpl = TemplateNotebook(
        organization_id=admin_user.organization_id,
        name="limma",
        category="differential_expression",
        notebook_path="notebooks/de_normalized_limma.ipynb",
        parameters_json={},
        is_builtin=True,
    )
    f = File(
        organization_id=admin_user.organization_id,
        filename="matrix.tsv",
        storage_uri="s3://x/m.tsv",
        file_type="table",
        source_type="external_deposit",
        artifact_type="deposited_matrix",
        uploader_user_id=admin_user.id,
    )
    session.add_all([tmpl, f])
    await session.flush()
    study = ValidationStudy(
        organization_id=admin_user.organization_id,
        requested_by_user_id=admin_user.id,
        state="reproducing",
        evidence_json={
            "route": "deposit",
            "deposit": {"files": [{"file_id": f.id, "filename": "matrix.tsv", "artifact_type": "deposited_matrix"}]},
            "deposit_inspection": {"value_type_observed": "tpm_or_cpm", "id_column": ""},
        },
    )
    session.add(study)
    await session.flush()
    plan = await ReproductionPlanService.create_plan(
        session, study, admin_user.id, pipeline_key="nf-core/rnaseq", differential_design=_P_DESIGN
    )
    plan.finding_claim_json = {
        "kind": "gene",
        "confirmed": True,
        "finding_set": {"kind": "gene", "namespace": "symbol", "entities": [], "n_sig": 0},
    }
    await session.flush()
    return study, plan


@pytest.mark.asyncio
async def test_a_p_value_claim_reaches_the_deposit_bundle_with_its_kind_and_operator(session, p_value_deposit_study):
    study, plan = p_value_deposit_study
    decision = await resolve_level3_from_deposit(session, study, plan)
    assert decision.inputs is not None, decision.reason
    assert decision.inputs["cutoffs"] == {"significance": _P, "effect": None}
    assert decision.inputs["cutoff_statement"] == "P < 0.01, no fold-change requirement"


# ---- the reproduced set is filtered by the same definition ----


def _driver_mocks(monkeypatch, table: str):
    async def _load(_session, sid):
        return SimpleNamespace(id=sid, status="completed")

    async def _poll(_session, cs):
        return SimpleNamespace(status="completed")

    async def _output(_session, cs):
        return table

    monkeypatch.setattr(ValidationDriverService, "_load_compute_session", _load)
    monkeypatch.setattr(NotebookExecutionService, "poll_execution", _poll)
    monkeypatch.setattr(ValidationDriverService, "_read_reproduction_output", _output)


async def _reproducing(session, admin_user, level3):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    study.state = "reproducing"
    study.evidence_json = {"level3": level3, "level3_run_session_id": 5}
    await session.flush()
    return study


_PAPER = FindingSet(kind="gene", namespace="symbol", entities=[FindingEntity("A", "up")])


@pytest.mark.asyncio
async def test_the_reproduced_set_is_filtered_at_the_stated_p_value(session, admin_user, monkeypatch):
    # A passes at P < 0.01 and fails at an adjusted P of 0.01; B the other way round.
    _driver_mocks(monkeypatch, "gene_id,log2FoldChange,pvalue,padj\nA,2.0,0.005,0.2\nB,-2.0,0.02,0.001\n")
    level3 = {
        "template_id": 1,
        "kind": "gene",
        "paper_finding_set": _PAPER.to_dict(),
        "parameters": {"lfc_threshold": 0.0, "padj_threshold": 0.01},
        "cutoffs": {"significance": _P, "effect": None},
    }
    study = await _reproducing(session, admin_user, level3)
    await ValidationDriverService._handle_reproducing(session, study)
    ours = study.evidence_json["level3_result"]["our_finding_set"]
    assert [e["id"] for e in ours["entities"]] == ["A"]


@pytest.mark.asyncio
async def test_a_reproduced_table_without_the_stated_measure_is_not_scored(session, admin_user, monkeypatch):
    """A table the stated definition cannot be applied to is not an empty result. Scoring it would
    report the paper's set as missed."""
    _driver_mocks(monkeypatch, "gene_id,log2FoldChange,padj\nA,2.0,0.001\n")
    level3 = {
        "template_id": 1,
        "kind": "gene",
        "paper_finding_set": _PAPER.to_dict(),
        "parameters": {"lfc_threshold": 0.0, "padj_threshold": 0.01},
        "cutoffs": {"significance": _P, "effect": None},
    }
    study = await _reproducing(session, admin_user, level3)
    await ValidationDriverService._handle_reproducing(session, study)
    assert "level3_result" not in study.evidence_json
    assert "P-value column" in study.evidence_json["level3_failed"]["reason"]


# ---- the author table is normalized at the stated definition, and the contrast is named ----


async def _plan_ready(session, admin_user, design):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    plan = await ReproductionPlanService.create_plan(
        session, study, admin_user.id, pipeline_key="nf-core/rnaseq", differential_design=design
    )
    for st in ("acquiring_text", "reading", "plan_ready"):
        await ValidationStudyService.transition(session, study.id, admin_user.organization_id, admin_user.id, st)
    await session.flush()
    return study, plan


@pytest.mark.asyncio
async def test_the_authors_table_is_filtered_at_the_stated_p_value(session, admin_user):
    study, plan = await _plan_ready(session, admin_user, _P_DESIGN)
    claim = await ReproductionPlanService.set_finding_claim(
        session,
        study.id,
        admin_user.organization_id,
        admin_user.id,
        kind="gene",
        table_text="gene,log2FoldChange,pvalue,padj\nA,2.0,0.005,0.2\nB,-2.0,0.02,0.001\n",
    )
    assert [e["id"] for e in claim["finding_set"]["entities"]] == ["A"]
    assert claim["cutoffs"] == {"significance": _P, "effect": None}
    # The legacy pair can hold only an adjusted P, so it never holds this one.
    assert claim["thresholds"]["padj"] is None


@pytest.mark.asyncio
async def test_the_finding_claim_names_its_contrast_rather_than_holding_the_dict(session, admin_user):
    study, plan = await _plan_ready(session, admin_user, _P_DESIGN)
    claim = await ReproductionPlanService.set_finding_claim(
        session,
        study.id,
        admin_user.organization_id,
        admin_user.id,
        kind="gene",
        table_text="gene,log2FoldChange,pvalue\nA,2.0,0.005\n",
    )
    assert claim["contrast"] == "KO vs WT"


@pytest.mark.asyncio
async def test_a_wide_tables_contrast_label_picks_its_columns(session, admin_user):
    """The caller's label for a multi-contrast table was shadowed by the selected contrast's dict, and
    `.strip()` then failed on it."""
    study, plan = await _plan_ready(session, admin_user, _P_DESIGN)
    wide = "gene,KO logFC,KO P.Value,HET logFC,HET P.Value\nA,2.0,0.001,0.1,0.9\n"
    claim = await ReproductionPlanService.set_finding_claim(
        session,
        study.id,
        admin_user.organization_id,
        admin_user.id,
        kind="gene",
        table_text=wide,
        contrast="KO",
    )
    assert claim["contrast"] == "KO"
    assert [e["id"] for e in claim["finding_set"]["entities"]] == ["A"]


@pytest.mark.asyncio
async def test_an_authors_table_without_the_stated_measure_asks_which_column_holds_it(session, admin_user):
    study, plan = await _plan_ready(session, admin_user, _P_DESIGN)
    claim = await ReproductionPlanService.set_finding_claim(
        session,
        study.id,
        admin_user.organization_id,
        admin_user.id,
        kind="gene",
        table_text="gene,log2FoldChange,padj\nA,2.0,0.001\n",
    )
    assert claim["finding_set"]["entities"] == []
    assert any("P-value column" in n for n in claim["finding_set"]["parse_notes"])
    assert "pval" in claim["needs_column_mapping"]["roles"]


# ---- the gate's design edit keeps the stated cutoffs ----


@pytest.mark.asyncio
async def test_the_gate_design_edit_keeps_the_stated_cutoffs(session, admin_user):
    study, plan = await _plan_ready(session, admin_user, _P_DESIGN)
    edited = {
        "contrasts": [
            {
                "name": "KO vs WT",
                "test_condition": "KO",
                "reference_condition": "WT",
                "test_samples": ["KO_1", "KO_2"],
                "reference_samples": ["WT_1", "WT_2"],
            }
        ],
        # The gate's two inputs are blank for a P value, because the legacy pair cannot hold one.
        "thresholds": {"log2fc": None, "padj": None},
    }
    saved = await ReproductionPlanService.set_differential_design(
        session, study.id, admin_user.organization_id, admin_user.id, edited, selected_contrast_index=0
    )
    contrast = saved.differential_design_json["contrasts"][0]
    assert contrast["cutoffs"] == [_P]
    assert analysis_cutoffs(contrast, saved.differential_design_json)["significance"] == _P


@pytest.mark.asyncio
async def test_a_person_who_types_a_different_cutoff_at_the_gate_is_the_one_recorded(session, admin_user):
    study, plan = await _plan_ready(session, admin_user, _P_DESIGN)
    edited = {
        "contrasts": [
            {
                "name": "KO vs WT",
                "test_samples": ["KO_1", "KO_2"],
                "reference_samples": ["WT_1", "WT_2"],
            }
        ],
        "thresholds": {"log2fc": 1.0, "padj": 0.05},
    }
    saved = await ReproductionPlanService.set_differential_design(
        session, study.id, admin_user.organization_id, admin_user.id, edited, selected_contrast_index=0
    )
    contrast = saved.differential_design_json["contrasts"][0]
    assert analysis_cutoffs(contrast, saved.differential_design_json)["significance"]["kind"] == "padj"
    assert contrast["cutoffs_decided_by"] == "human"


# ---- measurement: a linear fold change is converted to log2 ----


@pytest.mark.asyncio
async def test_a_twofold_claim_is_measured_as_an_absolute_log2_fold_change_above_one(session, admin_user):
    from app.services.validation_assessment import claimed_thresholds

    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    plan = await ReproductionPlanService.create_plan(session, study, admin_user.id, pipeline_key="nf-core/rnaseq")
    await ReproductionPlanService.add_comparison_targets(
        session,
        plan,
        [
            {"metric_key": "", "claim_text": "more than twofold", "threshold": 2.0, "threshold_kind": "fold_change"},
            {
                "metric_key": "",
                "claim_text": "at least threefold",
                "cutoffs": [{"kind": "fold_change", "operator": ">=", "value": 4}],
            },
        ],
    )
    await session.flush()
    assert await claimed_thresholds(session, study) == [1.0, 2.0]


# ---- display: the stated cutoff, in one vocabulary ----


def test_a_contrasts_stated_cutoff_is_described_in_words():
    assert contrast_cutoff_words(_P_DESIGN["contrasts"][0], _P_DESIGN) == "P < 0.01"


def test_the_report_shows_the_stated_cutoff_on_the_contrast_and_the_claim():
    summary = summarize(
        study={"state": "classified"},
        evidence={},
        plan={"differential_design": _P_DESIGN},
        targets=[{"claim_text": "257 genes up", "cutoffs": [_P], "contrast_index": 0, "bound_by": "model"}],
        issues=[],
    )
    [contrast] = summary["contrasts"]
    assert contrast["cutoff"] == "P < 0.01"
    assert "thresholds" not in contrast
    assert summary["claims"][0]["cutoff"] == "P < 0.01"


def test_the_report_describes_an_adjusted_p_in_the_same_words_everywhere():
    summary = summarize(
        study={"state": "classified"},
        evidence={},
        plan={"differential_design": {"contrasts": []}},
        targets=[
            {
                "claim_text": "x",
                "cutoffs": [
                    {"kind": "padj", "operator": "<", "value": 0.05},
                    {"kind": "abs_log2fc", "operator": ">", "value": 1},
                ],
                "bound_by": "model",
            }
        ],
        issues=[],
    )
    assert summary["claims"][0]["cutoff"] == "adjusted P < 0.05 and |log2FC| > 1"


# ---- attribution: a bundle that applied the stated cutoffs matched them ----


@pytest.mark.asyncio
async def test_a_reproduction_that_applied_the_stated_cutoffs_is_not_blamed_on_thresholds(session, admin_user):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await ReproductionPlanService.create_plan(
        session,
        study,
        admin_user.id,
        pipeline_key="nf-core/rnaseq",
        mapping_confidence="exact",
        reference_genome="GRCh38",
        differential_design=_P_DESIGN,
    )
    study.state = "comparing"
    study.evidence_json = {
        "computed_metrics": {},
        "comparison_targets": [],
        "level3": {"cutoffs": {"significance": _P, "effect": None}},
        "level3_result": {
            "concordance": {
                "kind": "gene",
                "verdict": "diverge",
                "paper_n": 100,
                "our_n": 90,
                "overlap": 8,
                "concordant": 8,
                "directional_overlap_frac": 0.08,
                "enrichment_p": 0.9,
                "notes": [],
            }
        },
    }
    await session.flush()
    await ValidationDriverService._handle_comparing(session, study)
    result = json.dumps(study.evidence_json["classification_result"])
    assert "did not apply the paper's stated significance" not in result


# ---- the gate is shown the stated cutoff, and the candidates are counted at it ----

_P_EXTRACTION = json.dumps(
    {
        "accessions": ["GSE1"],
        "method": {"assay": "bulk RNA-seq", "reference_build": "GRCh38"},
        "differential_design": {
            "contrasts": [
                {
                    "name": "KO vs WT",
                    "test_condition": "KO",
                    "reference_condition": "WT",
                    "assay": "bulk RNA-seq",
                    "finding_claim_index": 0,
                }
            ]
        },
        "claims": [
            {
                "metric_key": "",
                "claim_text": "257 genes were up",
                "value": 257,
                "output_type": "gene_set_size",
                "contrast": "KO vs WT",
                "cutoffs": [_P],
            }
        ],
        "data_availability": "deposited",
        "blockers": [],
    }
)


@pytest_asyncio.fixture
async def _lit_validation_on(session):
    from app.services import beta_features_service

    await beta_features_service.set_flag(session, "lit_validation", True)
    await session.commit()


def _fake_reader(monkeypatch):
    from app.services import validation_extraction_service as ext

    async def fake_get_active(sess, org_id):
        return SimpleNamespace(provider="anthropic", model="claude-opus-4-8", api_key=None)

    class _C:
        async def submit(self, prompt, payload, model, api_key, attachments=None):
            return "```json\n" + _P_EXTRACTION + "\n```"

    monkeypatch.setattr(ext.llm_provider_config_service, "get_active", fake_get_active)
    monkeypatch.setattr(ext, "get_client", lambda p: _C())


@pytest.mark.asyncio
async def test_the_plan_the_gate_reads_states_each_contrasts_cutoff_in_words(
    client, admin_token, monkeypatch, _lit_validation_on
):
    _fake_reader(monkeypatch)
    auth = {"Authorization": f"Bearer {admin_token}"}
    sid = (await client.post("/api/validation-studies", json={}, headers=auth)).json()["id"]
    r = await client.post(f"/api/validation-studies/{sid}/read", json={"full_text": "x"}, headers=auth)
    assert r.status_code == 200, r.text
    contrast = r.json()["plan"]["differential_design"]["contrasts"][0]
    assert contrast["stated_cutoff"] == "P < 0.01"


@pytest.mark.asyncio
async def test_candidate_tables_are_counted_at_the_stated_cutoff(client, admin_token, monkeypatch, _lit_validation_on):
    from app.services.literature.ground_truth_fetch_service import GroundTruthFetchService

    _fake_reader(monkeypatch)
    auth = {"Authorization": f"Bearer {admin_token}"}
    sid = (await client.post("/api/validation-studies", json={}, headers=auth)).json()["id"]
    await client.post(f"/api/validation-studies/{sid}/read", json={"full_text": "x"}, headers=auth)
    seen = {}

    async def _fake(accession, *, kind="gene", cutoffs=None, fetcher=None):
        seen["cutoffs"] = cutoffs
        return []

    monkeypatch.setattr(GroundTruthFetchService, "fetch_geo_candidates", _fake)
    r = await client.get(f"/api/validation-studies/{sid}/finding-set/candidates", headers=auth)
    assert r.status_code == 200, r.text
    assert seen["cutoffs"]["significance"] == _P


@pytest.mark.asyncio
async def test_a_candidate_is_counted_by_the_stated_measure_and_never_at_a_default():
    from app.services.literature.ground_truth_fetch_service import GroundTruthFetchService

    base = "https://ftp.ncbi.nlm.nih.gov/geo/series/GSEnnn/GSE1/suppl/"
    table = "gene,log2FoldChange,pvalue,padj\nA,2.0,0.005,0.2\nB,-2.0,0.02,0.001\n"
    pages = {base: '<a href="GSE1_DEG_results.csv">GSE1_DEG_results.csv</a>', base + "GSE1_DEG_results.csv": table}

    async def _fetch(url):
        return pages[url]

    stated = analysis_cutoffs({"cutoffs": [_P]}, {})
    [counted] = await GroundTruthFetchService.fetch_geo_candidates("GSE1", kind="gene", cutoffs=stated, fetcher=_fetch)
    assert counted["n_sig"] == 1

    refused = analysis_cutoffs({}, {})
    [uncounted] = await GroundTruthFetchService.fetch_geo_candidates(
        "GSE1", kind="gene", cutoffs=refused, fetcher=_fetch
    )
    assert uncounted["n_sig"] is None
    assert uncounted["table_text"] == table
    assert "significance cutoff is not stated" in uncounted["not_counted_because"]


# ---- the markdown export states the same cutoff ----


def test_the_markdown_level3_section_states_the_cutoff_the_reproduction_applied():
    from app.services.provenance.markdown_renderer import _append_level3_concordance

    parts: list[str] = []
    _append_level3_concordance(
        parts,
        {"differential_design": _P_DESIGN},
        {
            "level3": {"contrast": "KO vs WT", "cutoff_statement": "P < 0.01, no fold-change requirement"},
            "level3_result": {"concordance": {"verdict": "agree", "paper_n": 3, "our_n": 3, "concordant": 3}},
        },
    )
    text = "\n".join(parts)
    assert "P < 0.01, no fold-change requirement" in text
    assert "padj <=" not in text


def test_the_markdown_claims_table_uses_the_same_vocabulary():
    from app.services.provenance.markdown_renderer import _cutoff

    assert _cutoff({"cutoffs": [_P]}) == "P < 0.01"
    assert _cutoff({"threshold": 0.05, "threshold_kind": "padj"}) == "adjusted P 0.05"
    assert _cutoff({}) == "--"
