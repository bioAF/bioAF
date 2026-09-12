"""change_7.5 section 2.2: each claim is linked to the experiment it was measured in.

Study 38's paper ran ChIP-seq and bulk RNA-seq. The extraction held one paper-level `method.assay` for
both, the mapper answered nf-core/chipseq, and both RNA-seq contrasts were then refused. Nothing
anywhere represented an experiment. A Reported Experiment (glossary) is one experiment a paper
reports: one assay, its conditions, its reference and its deposited data.

Validation is deterministic: every claim and every contrast belongs to exactly one experiment, every
experiment names one assay, and one experiment naming two assays is a blocker, never a guess.
"""

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.comparison_target import ComparisonTarget
from app.services import validation_extraction_service as ext
from app.services.reported_experiments import (
    LEGACY_UNVERIFIED,
    experiments_for_plan,
    legacy_needs_renewed_selection,
    normalize_reported_experiments,
)
from app.services.validation_study_service import ValidationStudyService


def _norm(raw, claims=2, contrasts=2):
    return normalize_reported_experiments(raw, claim_count=claims, contrast_count=contrasts)


_TWO = [
    {"id": "e1", "assay": "ChIP-seq", "claim_indices": [0], "contrast_indices": []},
    {"id": "e2", "assay": "bulk RNA-seq", "claim_indices": [1], "contrast_indices": [0, 1]},
]


def test_two_experiments_link_their_own_claims_and_contrasts():
    out = _norm(_TWO)
    assert [e["id"] for e in out.experiments] == ["e1", "e2"]
    assert out.claim_experiment == {0: "e1", 1: "e2"}
    assert out.contrast_experiment == {0: "e2", 1: "e2"}
    assert out.blockers == []


def test_a_claim_in_two_experiments_is_a_blocker_and_is_linked_to_neither():
    raw = [
        {"id": "e1", "assay": "ChIP-seq", "claim_indices": [0, 1], "contrast_indices": []},
        {"id": "e2", "assay": "bulk RNA-seq", "claim_indices": [1], "contrast_indices": [0, 1]},
    ]
    out = _norm(raw)
    assert 1 not in out.claim_experiment
    assert any("claim 1" in b.lower() and "more than one" in b.lower() for b in out.blockers)


def test_a_claim_in_no_experiment_is_a_blocker():
    raw = [{"id": "e1", "assay": "bulk RNA-seq", "claim_indices": [0], "contrast_indices": [0, 1]}]
    out = _norm(raw)
    assert 1 not in out.claim_experiment
    assert any("claim 1" in b.lower() and "no experiment" in b.lower() for b in out.blockers)


def test_every_contrast_belongs_to_exactly_one_experiment():
    raw = [
        {"id": "e1", "assay": "ChIP-seq", "claim_indices": [0], "contrast_indices": [0]},
        {"id": "e2", "assay": "bulk RNA-seq", "claim_indices": [1], "contrast_indices": [0]},
    ]
    out = _norm(raw)
    assert 0 not in out.contrast_experiment
    assert any("contrast 0" in b.lower() for b in out.blockers)
    assert any("contrast 1" in b.lower() and "no experiment" in b.lower() for b in out.blockers)


def test_an_experiment_with_no_assay_is_a_blocker():
    out = _norm([{"id": "e1", "assay": "", "claim_indices": [0, 1], "contrast_indices": [0, 1]}])
    assert any("names no assay" in b for b in out.blockers)


def test_one_experiment_naming_two_assays_is_a_blocker_never_a_guess():
    out = _norm(
        [{"id": "e1", "assay": "ChIP-seq and bulk RNA-seq", "claim_indices": [0, 1], "contrast_indices": [0, 1]}]
    )
    assert any("more than one assay" in b for b in out.blockers)
    assert out.experiments[0]["assay_ambiguous"] is True


def test_duplicate_or_missing_ids_are_given_their_own():
    out = _norm(
        [
            {"assay": "ChIP-seq", "claim_indices": [0], "contrast_indices": []},
            {"id": "x", "assay": "bulk RNA-seq", "claim_indices": [1], "contrast_indices": [0]},
            {"id": "x", "assay": "ATAC-seq", "claim_indices": [], "contrast_indices": [1]},
        ]
    )
    ids = [e["id"] for e in out.experiments]
    assert len(set(ids)) == 3


def test_an_experiment_keeps_its_reference_as_two_stated_parts():
    out = _norm(
        [
            {
                "id": "e1",
                "assay": "bulk RNA-seq",
                "claim_indices": [0, 1],
                "contrast_indices": [0, 1],
                "reference": {
                    "assembly": "",
                    "assembly_quote": "",
                    "annotation": "GENCODE M23",
                    "annotation_quote": "reads were quantified against GENCODE M23",
                },
            }
        ]
    )
    ref = out.experiments[0]["reference"]
    assert ref["annotation"]["stated"] == "GENCODE M23"
    assert ref["annotation"]["quote"] == "reads were quantified against GENCODE M23"
    assert ref["assembly"]["stated"] is None


# ---- legacy plans ----


def _plan(**kw):
    return SimpleNamespace(
        reported_experiments_json=kw.get("experiments"),
        differential_design_json=kw.get("design"),
        pipeline_key=kw.get("pipeline_key", "nf-core/rnaseq"),
        reference_build=kw.get("reference_build"),
        analysis_selection_json=kw.get("selection"),
        tools_json=[],
    )


def test_a_legacy_plan_reads_as_one_unverified_experiment():
    design = {"contrasts": [{"name": "a", "assay": "bulk RNA-seq"}]}
    [experiment] = experiments_for_plan(_plan(design=design))
    assert experiment["status"] == LEGACY_UNVERIFIED
    assert experiment["contrast_indices"] == [0]
    assert experiment["assay_ambiguous"] is False


def test_a_legacy_plan_whose_contrasts_span_two_assays_is_ambiguous_and_needs_renewed_selection():
    design = {"contrasts": [{"name": "a", "assay": "bulk RNA-seq"}, {"name": "b", "assay": "ChIP-seq"}]}
    plan = _plan(design=design)
    [experiment] = experiments_for_plan(plan)
    assert experiment["assay_ambiguous"] is True
    assert legacy_needs_renewed_selection(plan) is True


def test_an_unambiguous_legacy_plan_may_resume():
    plan = _plan(design={"contrasts": [{"name": "a", "assay": "bulk RNA-seq"}]})
    assert legacy_needs_renewed_selection(plan) is False


def test_a_new_plan_never_needs_the_legacy_rule():
    plan = _plan(experiments=[{"id": "e1", "assay": "bulk RNA-seq"}], design={"contrasts": []})
    assert legacy_needs_renewed_selection(plan) is False


# ---- the extraction records experiments on the plan and each claim ----

_EXTRACTION = {
    "accessions": ["GSE1"],
    "method": {"assay": "ChIP-seq and bulk RNA-seq", "reference_build": "mm9 / GENCODE M23"},
    "reported_experiments": [
        {
            "id": "e1",
            "assay": "ChIP-seq",
            "claim_indices": [0],
            "contrast_indices": [],
            "reference": {"assembly": "mm9", "assembly_quote": "aligned to mm9"},
        },
        {
            "id": "e2",
            "assay": "bulk RNA-seq",
            "claim_indices": [1],
            "contrast_indices": [0],
            "reference": {"annotation": "GENCODE M23", "annotation_quote": "quantified against GENCODE M23"},
        },
    ],
    "differential_design": {
        "contrasts": [
            {"name": "KO vs WT", "test_condition": "KO", "reference_condition": "WT", "assay": "bulk RNA-seq"}
        ]
    },
    "claims": [
        {"metric_key": "peak_count", "claim_text": "we called many peaks", "value": 1000, "unit": "peaks"},
        {
            "metric_key": "",
            "claim_text": "genes were up",
            "value": 100,
            "output_type": "gene_set_size",
            "contrast": "KO vs WT",
            "cutoffs": [{"kind": "pvalue", "operator": "<", "value": 0.01}],
        },
    ],
    "data_availability": "deposited",
    "blockers": [],
}


def _patch(monkeypatch, extraction):
    async def fake_get_active(sess, org_id):
        return SimpleNamespace(provider="anthropic", model="claude-opus-4-8", api_key=None)

    class _C:
        async def submit(self, prompt, payload, model, api_key, attachments=None):
            return "```json\n" + json.dumps(extraction) + "\n```"

    monkeypatch.setattr(ext.llm_provider_config_service, "get_active", fake_get_active)
    monkeypatch.setattr(ext, "get_client", lambda p: _C())

    async def _bind(claims, *, client, model, api_key, on_issue=None, **_):
        return [
            {"claim_index": i, "bound_key": None, "reason": "declined", "confidence": 0.9, "declined": True}
            for i in range(len(claims))
        ]

    monkeypatch.setattr(ext, "bind_claims", _bind)


@pytest.mark.asyncio
async def test_the_plan_and_each_claim_carry_their_experiment(session, admin_user, monkeypatch):
    _patch(monkeypatch, _EXTRACTION)
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    plan = await ext.ValidationExtractionService.extract(
        session, study, "text", admin_user.organization_id, admin_user.id
    )
    await session.flush()

    assert [e["id"] for e in plan.reported_experiments_json] == ["e1", "e2"]
    targets = (
        (
            await session.execute(
                select(ComparisonTarget)
                .where(ComparisonTarget.reproduction_plan_id == plan.id)
                .order_by(ComparisonTarget.id)
            )
        )
        .scalars()
        .all()
    )
    assert [t.reported_experiment_id for t in targets] == ["e1", "e2"]
    assert plan.differential_design_json["contrasts"][0]["reported_experiment_id"] == "e2"


def test_the_extraction_prompt_asks_for_each_experiment_separately():
    system, _ = ext.build_extraction_prompt("")
    assert '"reported_experiments"' in system
    assert "one assay" in system.lower()
