"""change_7.5 section 3.4: processed reanalysis is compared with the claim.

`resolve_level3_from_deposit` declined every deposit that had no person-confirmed finding set, so an
autonomous study could download and map a matrix and still compare nothing. In autonomous mode the
authors' result table identified with the input (section 3.2) becomes the ground-truth set, at the
claim's own predicate; with no authors' table the reanalysis still reports its count against the
claim, and no concordance. The same predicate is applied to both sets, the concordance is filtered by
the claim's direction, and the count is labelled as coming from a different method.
"""

import pytest
import pytest_asyncio

from app.models.file import File
from app.models.organization import Organization
from app.models.template_notebook import TemplateNotebook
from app.models.validation_study import ValidationStudy
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.result_set_normalizer import FindingEntity, FindingSet
from app.services.validation_level3_service import resolve_level3_from_deposit, score_reproduction

_P = [{"kind": "pvalue", "operator": "<", "value": 0.01}]
_COLUMNS = ["WT1", "WT2", "KO1", "KO2"]
_DESIGN = {
    "selected_contrast": {"contrast_index": 0, "decided_by": "claim_selection"},
    "contrasts": [{"name": "KO vs WT", "test_condition": "KO", "reference_condition": "WT", "cutoffs": _P,
                   "test_samples": ["KO1", "KO2"], "reference_samples": ["WT1", "WT2"],
                   "units": {"KO1": "k1", "KO2": "k2", "WT1": "w1", "WT2": "w2"}}],
}
_PREDICATE = {"significance": {"kind": "pvalue", "operator": "<", "value": 0.01, "adjustment": None},
              "effect": {"kind": "none"}, "direction": "up",
              "count": {"relation": "=", "value": 2.0, "tolerance": None, "entity": "gene"}, "status": "resolved"}
_SELECTION = {"current": {"revision": 2, "claim_index": 0, "check": "processed_reanalysis", "contrast_index": 0,
                          "predicate": _PREDICATE, "predicate_words": "KO versus WT, P < 0.01, up, no fold-change requirement"}}
_AUTHOR_TABLE = (
    "gene\tlog2FoldChange\tpvalue\tpadj\n"
    "g1\t1.5\t0.001\t0.02\n"
    "g2\t2.0\t0.005\t0.03\n"
    "g3\t-1.0\t0.001\t0.02\n"  # down: not the claim's direction
    "g4\t1.0\t0.2\t0.5\n"
)


class _Storage:
    def __init__(self, files):
        self.files = files

    async def read_text(self, uri, *, encoding="utf-8"):
        return self.files[uri]


@pytest_asyncio.fixture
async def deposit(session, admin_user):
    template = TemplateNotebook(organization_id=admin_user.organization_id, name="limma", category="differential_expression",
                                notebook_path="notebooks/de_normalized_limma.ipynb", parameters_json={}, is_builtin=True)
    matrix = File(organization_id=admin_user.organization_id, filename="GSE1_norm.txt", storage_uri="s3://x/m.tsv",
                  file_type="table", source_type="external_deposit", artifact_type="deposited_matrix",
                  uploader_user_id=admin_user.id)
    session.add_all([template, matrix])
    await session.flush()
    return matrix


async def _study(session, admin_user, matrix, *, autonomous=True, author_table=True, design=None):
    org = await session.get(Organization, admin_user.organization_id)
    org.lit_validation_autonomy = "autonomous" if autonomous else "assisted"
    files = [{"file_id": matrix.id, "filename": "GSE1_norm.txt", "storage_uri": "s3://x/m.tsv", "artifact_type": "deposited_matrix"}]
    if author_table:
        files.append({"filename": "GSE1_DESeq2.txt", "storage_uri": "s3://x/t.tsv", "artifact_type": "deposited_result_table"})
    study = ValidationStudy(
        organization_id=admin_user.organization_id, requested_by_user_id=admin_user.id, state="inspecting_deposit",
        evidence_json={
            "route": "deposit",
            "deposit": {"files": files},
            "deposit_inspection": {"value_type_observed": "normalized_other", "id_column": "", "columns": _COLUMNS},
            "input_choice": {"author_table": "GSE1_DESeq2.txt" if author_table else None, "decided_by": "model"},
        },
    )
    session.add(study)
    await session.flush()
    plan = await ReproductionPlanService.create_plan(
        session, study, admin_user.id, pipeline_key="nf-core/rnaseq", differential_design=design or _DESIGN,
        analysis_selection=_SELECTION,
    )
    await session.flush()
    return study, plan


@pytest.mark.asyncio
async def test_autonomous_mode_compares_against_the_identified_authors_table(session, admin_user, deposit):
    study, plan = await _study(session, admin_user, deposit)
    decision = await resolve_level3_from_deposit(
        session, study, plan, evidence=study.evidence_json, storage_adapter=_Storage({"s3://x/t.tsv": _AUTHOR_TABLE})
    )
    assert decision.inputs is not None, decision.reason
    ground_truth = decision.inputs["ground_truth"]
    assert ground_truth["source"] == "identified_author_table"
    assert ground_truth["file"] == "GSE1_DESeq2.txt"
    # The claim's own predicate: P < 0.01 on the P-value column, every direction kept on the set.
    ids = {e["id"] for e in decision.inputs["paper_finding_set"]["entities"]}
    assert ids == {"g1", "g2", "g3"}
    assert decision.inputs["predicate"] == _PREDICATE


@pytest.mark.asyncio
async def test_with_no_authors_table_the_reanalysis_still_runs_with_no_ground_truth(session, admin_user, deposit):
    study, plan = await _study(session, admin_user, deposit, author_table=False)
    decision = await resolve_level3_from_deposit(session, study, plan, evidence=study.evidence_json)
    assert decision.inputs is not None, decision.reason
    assert decision.inputs["paper_finding_set"] is None
    assert decision.inputs["ground_truth"] is None


@pytest.mark.asyncio
async def test_assisted_mode_still_waits_for_a_person_to_confirm_the_table(session, admin_user, deposit):
    study, plan = await _study(session, admin_user, deposit, autonomous=False)
    decision = await resolve_level3_from_deposit(
        session, study, plan, evidence=study.evidence_json, storage_adapter=_Storage({"s3://x/t.tsv": _AUTHOR_TABLE})
    )
    assert decision.inputs is None
    assert decision.reason_code == "no_finding_claim"


@pytest.mark.asyncio
async def test_confirmed_technical_groups_reach_the_template_aligned_to_the_arms(session, admin_user, deposit):
    design = {**_DESIGN, "contrasts": [{**_DESIGN["contrasts"][0], "test_samples": ["KO1", "KO1b", "KO2"],
                                        "units": {"KO1": "k1", "KO1b": "k1", "KO2": "k2", "WT1": "w1", "WT2": "w2"},
                                        "technical_groups": {"KO1": "k1s", "KO1b": "k1s"}}]}
    study, plan = await _study(session, admin_user, deposit, author_table=False, design=design)
    decision = await resolve_level3_from_deposit(session, study, plan, evidence=study.evidence_json)
    assert decision.inputs["parameters"]["technical_group_labels"] == "k1s,k1s,KO2,WT1,WT2"
    assert decision.inputs["collapse"]["rule"] == "mean on the stored scale"


# ---- scoring ----


def _set(*entities):
    return FindingSet(kind="gene", namespace="symbol", entities=[FindingEntity(id=i, direction=d) for i, d in entities])


def test_the_concordance_is_filtered_by_the_claims_direction_and_the_count_sits_beside_it():
    level3 = {"kind": "gene", "predicate": _PREDICATE,
              "paper_finding_set": _set(("g1", "up"), ("g2", "up"), ("g3", "down")).to_dict()}
    ours = _set(("g1", "up"), ("g2", "up"), ("g5", "down"))
    result = score_reproduction(level3, ours, universe=100)
    assert result["concordance"]["paper_n"] == 2  # the down gene is not the claim's
    assert result["concordance"]["our_n"] == 2
    count = result["claim_count"]
    assert count["count"] == 2
    assert count["status"] == "holds"
    assert "different method" in count["label"]


def test_with_no_ground_truth_the_count_is_reported_and_no_concordance():
    level3 = {"kind": "gene", "predicate": _PREDICATE, "paper_finding_set": None}
    result = score_reproduction(level3, _set(("g1", "up")), universe=100)
    assert result["concordance"] is None
    assert result["claim_count"]["count"] == 1
    assert result["claim_count"]["status"] == "fails"
