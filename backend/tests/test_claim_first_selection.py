"""change_7.5 section 2.6: select the claim and its check together, then the workflow from its experiment.

Study 38 chose nf-core/chipseq from one paper-level method, then found both RNA-seq contrasts
incompatible with it and ran nothing. The workflow has to follow the selection of a claim and a check,
from that claim's experiment alone. The stage 1 compatibility checks still run on the selection.
"""

import json
from types import SimpleNamespace

import pytest

from app.services.validation_checks import AUTHOR_RESULTS, PROCESSED_REANALYSIS, RAW_REANALYSIS
from app.services.validation_selection import rank_candidates, select_analysis

_P = [{"kind": "pvalue", "operator": "<", "value": 0.01}]


def _targets():
    return [
        {"metric_key": "peak_count", "claim_text": "peaks were called", "claimed_value": 8000, "unit": "peaks",
         "reported_experiment_id": "e1", "bound_by": "model"},
        {"claim_text": "genes were up", "claimed_value": 257, "output_type": "gene_set_size", "contrast_index": 0,
         "cutoffs": _P, "reported_experiment_id": "e2", "bound_by": "model"},
        {"claim_text": "genes were down", "claimed_value": 524, "output_type": "gene_set_size", "contrast_index": 0,
         "cutoffs": _P, "reported_experiment_id": "e2", "bound_by": "model"},
    ]


def _unavailable(reason="x", requirement="r"):
    return {"status": "unavailable", "reason": reason, "requirement": requirement}


def _checks():
    pending = {"status": "unresolved", "reason": "mapping", "requirement": "sample_mapping"}
    table = {"status": "available", "reason": "the authors published t.txt.gz", "requirement": None}
    return [
        {"qc_metric": _unavailable(), AUTHOR_RESULTS: _unavailable(), PROCESSED_REANALYSIS: _unavailable(),
         RAW_REANALYSIS: _unavailable("the paper states mm9", "reference")},
        {"qc_metric": _unavailable(), AUTHOR_RESULTS: table, PROCESSED_REANALYSIS: pending,
         RAW_REANALYSIS: _unavailable("GENCODE M23", "reference")},
        {"qc_metric": _unavailable(), AUTHOR_RESULTS: table, PROCESSED_REANALYSIS: pending,
         RAW_REANALYSIS: _unavailable("GENCODE M23", "reference")},
    ]


_EXPERIMENTS = [
    {"id": "e1", "assay": "ChIP-seq", "workflow": "nf-core/chipseq",
     "reference": {"assembly": {"status": "unavailable", "stated": "mm9"}, "annotation": {"status": "unstated"}}},
    {"id": "e2", "assay": "bulk RNA-seq", "workflow": "nf-core/rnaseq",
     "reference": {"assembly": {"status": "usable", "resolved": "GRCm38"},
                   "annotation": {"status": "unavailable", "stated": "GENCODE M23"}}},
]
_CONTRASTS = [{"name": "KO vs WT", "assay": "bulk RNA-seq", "cutoffs": _P, "reported_experiment_id": "e2"}]


class _Client:
    def __init__(self, answer):
        self.answer = answer
        self.calls = 0

    async def submit(self, prompt, payload, model, api_key, attachments=None):
        self.calls += 1
        self.payload = payload
        return "```json\n" + json.dumps(self.answer) + "\n```"


async def _select(*, autonomous, client=None, route="deposit", targets=None, checks=None):
    return await select_analysis(
        targets or _targets(),
        checks or _checks(),
        experiments=_EXPERIMENTS,
        contrasts=_CONTRASTS,
        route=route,
        autonomous=autonomous,
        client=client or _Client({}),
        model="m",
        api_key=None,
    )


def test_a_claim_with_an_authors_table_ranks_first():
    ranked = rank_candidates(
        [
            {"claim_index": 0, "check": "processed_reanalysis", "status": "unresolved", "requirement": "deposit_listing"},
            {"claim_index": 1, "check": "processed_reanalysis", "status": "unresolved", "requirement": "sample_mapping"},
        ],
        _checks(),
    )
    assert ranked[0]["claim_index"] == 1


@pytest.mark.asyncio
async def test_the_workflow_follows_the_selected_claims_experiment():
    selection = await _select(autonomous=False)
    current = selection["current"]
    assert current["claim_index"] in (1, 2)
    assert current["check"] == PROCESSED_REANALYSIS
    assert current["reported_experiment_id"] == "e2"
    assert current["workflow"] == "nf-core/rnaseq"
    assert current["contrast_index"] == 0
    assert current["revision"] == 1


@pytest.mark.asyncio
async def test_assisted_mode_proposes_for_a_person_and_asks_no_model():
    client = _Client({"claim_index": 2, "check": PROCESSED_REANALYSIS, "reason": "r", "confidence": 0.9})
    selection = await _select(autonomous=False, client=client)
    assert client.calls == 0
    assert selection["current"]["decided_by"] == "proposal"


@pytest.mark.asyncio
async def test_autonomous_mode_asks_the_model_among_the_candidates():
    client = _Client({"claim_index": 2, "check": PROCESSED_REANALYSIS, "reason": "the down count", "confidence": 0.8})
    selection = await _select(autonomous=True, client=client)
    assert client.calls == 1
    assert "genes were down" in client.payload
    current = selection["current"]
    assert (current["claim_index"], current["decided_by"], current["confidence"]) == (2, "model", 0.8)


@pytest.mark.asyncio
async def test_a_model_answer_outside_the_candidates_falls_back_to_the_ranking():
    client = _Client({"claim_index": 0, "check": RAW_REANALYSIS, "reason": "r", "confidence": 0.9})
    selection = await _select(autonomous=True, client=client)
    assert selection["current"]["claim_index"] in (1, 2)
    assert selection["current"]["decided_by"] == "ranking"


@pytest.mark.asyncio
async def test_unselected_claims_are_unassessed_with_their_reason():
    selection = await _select(autonomous=False)
    unassessed = {row["claim_index"]: row["reason"] for row in selection["unassessed"]}
    assert set(unassessed) == {0, 1, 2} - {selection["current"]["claim_index"]}
    assert "the paper states mm9" in unassessed[0]


@pytest.mark.asyncio
async def test_no_candidate_is_a_recorded_null_with_every_claim_unassessed():
    selection = await _select(autonomous=False, route="pipeline")
    assert selection["current"] is None
    assert {row["claim_index"] for row in selection["unassessed"]} == {0, 1, 2}


@pytest.mark.asyncio
async def test_the_stage_1_compatibility_check_runs_on_the_selection():
    contrasts = [{"name": "KO vs WT", "assay": "ChIP-seq", "cutoffs": _P, "reported_experiment_id": "e2"}]
    selection = await select_analysis(
        _targets(),
        _checks(),
        experiments=_EXPERIMENTS,
        contrasts=contrasts,
        route="deposit",
        autonomous=False,
        client=_Client({}),
        model="m",
        api_key=None,
    )
    assert selection["current"] is None
    assert "no_compatible_contrast" in json.dumps(selection)


# ---- the plan carries the selection ----

_EXTRACTION = {
    "accessions": ["GSE1"],
    "method": {"assay": "ChIP-seq and bulk RNA-seq"},
    "reported_experiments": [
        {"id": "e1", "assay": "ChIP-seq", "claim_indices": [0], "contrast_indices": [], "resources": ["GSE1"],
         "reference": {"assembly": "mm9", "assembly_quote": "aligned to mm9"}},
        {"id": "e2", "assay": "bulk RNA-seq", "claim_indices": [1, 2], "contrast_indices": [0], "resources": ["GSE1"],
         "reference": {"annotation": "GENCODE M23", "annotation_quote": "quantified against GENCODE M23"}},
    ],
    "differential_design": {"contrasts": [{"name": "KO vs WT", "test_condition": "KO", "reference_condition": "WT",
                                           "assay": "bulk RNA-seq", "finding_claim_index": 1}]},
    "claims": [
        {"metric_key": "peak_count", "claim_text": "peaks were called", "value": 8000, "unit": "peaks"},
        {"metric_key": "", "claim_text": "genes were up", "value": 257, "output_type": "gene_set_size",
         "contrast": "KO vs WT", "cutoffs": _P},
        {"metric_key": "", "claim_text": "genes were down", "value": 524, "output_type": "gene_set_size",
         "contrast": "KO vs WT", "cutoffs": _P},
    ],
    "data_availability": "deposited",
    "blockers": [],
}

_CAPABILITIES = {
    "deposits": [
        {"accession": "GSE1", "archive": "geo", "exists": "yes", "access": "public", "supported": "yes",
         "raw_data": "yes", "preprocessed_data": "yes", "registered_samples": 20,
         "result_tables": ["GSE1_DeSeq2.txt.gz"],
         "listing": {"kinds": {"coverage": 12, "matrix_normalized": 1, "de_table": 1},
                     "library_strategies": ["ChIP-Seq", "RNA-Seq"]}}
    ]
}


def _patch(monkeypatch):
    from app.services import validation_extraction_service as ext

    async def fake_get_active(sess, org_id):
        return SimpleNamespace(provider="anthropic", model="claude-opus-4-8", api_key=None)

    class _C:
        async def submit(self, prompt, payload, model, api_key, attachments=None):
            return "```json\n" + json.dumps(_EXTRACTION) + "\n```"

    monkeypatch.setattr(ext.llm_provider_config_service, "get_active", fake_get_active)
    monkeypatch.setattr(ext, "get_client", lambda p: _C())

    async def _bind(claims, *, client, model, api_key, on_issue=None, **_):
        return [{"claim_index": i, "bound_key": None, "reason": "declined", "confidence": 0.9, "declined": True}
                for i in range(len(claims))]

    monkeypatch.setattr(ext, "bind_claims", _bind)
    return ext


@pytest.mark.asyncio
async def test_the_plans_workflow_is_the_selected_one_and_its_checks_are_stored(session, admin_user, monkeypatch):
    from sqlalchemy import select

    from app.models.comparison_target import ComparisonTarget
    from app.services.validation_study_service import ValidationStudyService

    ext = _patch(monkeypatch)
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, intended_route="deposit"
    )
    await session.flush()

    async def _discover(plan):
        return _CAPABILITIES

    plan = await ext.ValidationExtractionService.extract(
        session, study, "text", admin_user.organization_id, admin_user.id, discover=_discover
    )
    await session.flush()

    assert plan.pipeline_key == "nf-core/rnaseq"
    current = plan.analysis_selection_json["current"]
    assert current["reported_experiment_id"] == "e2"
    assert current["check"] == PROCESSED_REANALYSIS
    assert plan.differential_design_json["selected_contrast"]["contrast_index"] == 0

    targets = (
        await session.execute(
            select(ComparisonTarget).where(ComparisonTarget.reproduction_plan_id == plan.id).order_by(ComparisonTarget.id)
        )
    ).scalars().all()
    de = targets[1]
    assert de.checks["qc_metric"]["status"] == "unavailable"
    assert de.checks["author_results"]["status"] == "available"
    assert de.checks["raw_reanalysis"]["status"] == "unavailable"
    assert "GENCODE M23" in de.checks["raw_reanalysis"]["reason"]
    assert targets[0].checks["raw_reanalysis"]["status"] == "unavailable"
    assert {r["identifier"] for r in plan.resources_json} >= {"GSE1"}


def _facts(scope):
    return {
        "population": {"value": "the knockout ChIP-seq sample", "scope": scope, "quote": "in the KO ChIP"},
        "aggregation": {"value": "per_sample", "quote": "we called peaks in the KO ChIP"},
        "denominator": {"value": None, "quote": None},
    }


@pytest.mark.asyncio
async def test_a_binding_whose_facts_do_not_match_is_stored_unbound_with_its_reason(session, admin_user, monkeypatch):
    from sqlalchemy import select

    from app.models.comparison_target import ComparisonTarget
    from app.services.validation_study_service import ValidationStudyService

    ext = _patch(monkeypatch)

    async def _bind(claims, *, client, model, api_key, on_issue=None, **_):
        rows = [{"claim_index": i, "bound_key": None, "reason": "declined", "confidence": 0.9, "declined": True,
                 "facts": _facts("not_stated")} for i in range(len(claims))]
        rows[0] = {"claim_index": 0, "bound_key": "peak_count", "reason": "peaks", "confidence": 0.9,
                   "declined": False, "facts": _facts("one_sample")}
        return rows

    monkeypatch.setattr(ext, "bind_claims", _bind)
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, intended_route="deposit"
    )
    await session.flush()

    async def _discover(plan):
        return _CAPABILITIES

    plan = await ext.ValidationExtractionService.extract(
        session, study, "text", admin_user.organization_id, admin_user.id, discover=_discover
    )
    await session.flush()
    peaks = (
        await session.execute(
            select(ComparisonTarget).where(ComparisonTarget.reproduction_plan_id == plan.id).order_by(ComparisonTarget.id)
        )
    ).scalars().first()
    assert peaks.bound_key is None
    assert peaks.bound_by == "model"
    assert peaks.aggregation == "per_sample"
    assert peaks.binding_facts["proposed_key"] == "peak_count"
    assert peaks.binding_facts["population"]["quote"] == "in the KO ChIP"
    assert peaks.checks["qc_metric"]["status"] == "unavailable"
    assert "one sample" in peaks.checks["qc_metric"]["reason"]


@pytest.mark.asyncio
async def test_each_experiment_carries_its_own_workflow_and_reference(session, admin_user, monkeypatch):
    from app.services.validation_study_service import ValidationStudyService

    ext = _patch(monkeypatch)
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, intended_route="deposit"
    )
    await session.flush()
    plan = await ext.ValidationExtractionService.extract(
        session, study, "text", admin_user.organization_id, admin_user.id
    )
    by_id = {e["id"]: e for e in plan.reported_experiments_json}
    assert by_id["e1"]["workflow"] == "nf-core/chipseq"
    assert by_id["e2"]["workflow"] == "nf-core/rnaseq"
    assert by_id["e1"]["reference"]["assembly"]["status"] == "unavailable"
    assert by_id["e2"]["reference"]["assembly"]["resolved"] == "GRCm38"
    assert by_id["e2"]["reference"]["assembly"]["established_from"] == "annotation release"
    assert by_id["e2"]["reference"]["annotation"]["status"] == "unavailable"
    # No paper-level default: the plan carries the selected experiment's stated reference.
    assert plan.reference_build and "GENCODE M23" in plan.reference_build


# ---- deposit selection is told the experiment and the claim ----


def test_deposit_selection_is_told_the_experiments_assay_and_the_selected_claim():
    from app.services.deposit_selection import build_selection_prompt
    from app.services.literature.deposit_inventory_service import DepositEntry

    entry = DepositEntry(filename="GSE1_counts.txt.gz", url="https://x/GSE1_counts.txt.gz",
                         classification="matrix_counts", level="series", size_bytes=1000)
    _system, payload = build_selection_prompt(
        [entry], pipeline_key="nf-core/rnaseq", kind="gene",
        experiment={"id": "e2", "assay": "bulk RNA-seq"}, claim="257 genes were up in KO",
    )
    assert "bulk RNA-seq" in payload and "e2" in payload
    assert "257 genes were up in KO" in payload
    assert "The paper's assay maps to" not in payload
