"""change_7.5 section 2.6 (7.4 section 2.2's rules, carried forward): a selection is revised, never
overwritten in place, and a change invalidates exactly what depends on it.

A stale artifact (computed for an earlier revision) is never reused and never rendered as current; the
report shows it as history. "Review and resume" invalidates by revision.
"""

from types import SimpleNamespace

import pytest

from app.services.validation_revisions import (
    invalidate,
    revise,
    stale_artifacts,
    sync_revisions,
)


def _record(**current):
    base = {"revision": 1, "claim_index": 1, "check": "processed_reanalysis", "contrast_index": 0, "input": None,
            "sample_mapping": None, "predicate": None, "decided_by": "model", "reason": "r", "confidence": 0.8}
    return {"current": {**base, **current}, "history": [], "unassessed": []}


def _study(**evidence):
    return SimpleNamespace(evidence_json=dict(evidence), analysis_run_id=None)


def _plan(record, finding_claim=None):
    return SimpleNamespace(analysis_selection_json=record, finding_claim_json=finding_claim)


def test_a_revision_supersedes_the_current_one_and_keeps_it_as_history():
    record, changed = revise(_record(), {"contrast_index": 1, "claim_index": 2}, decided_by="human", reason="chosen at the gate")
    assert changed == {"claim", "contrast"}
    assert record["current"]["revision"] == 2
    assert record["current"]["decided_by"] == "human"
    assert record["history"][0]["revision"] == 1 and record["history"][0]["superseded"] is True


def test_an_unchanged_selection_is_not_a_new_revision():
    record, changed = revise(_record(), {"contrast_index": 0}, decided_by="human", reason="same")
    assert changed == set()
    assert record["current"]["revision"] == 1


def test_a_changed_contrast_invalidates_every_dependent_artifact():
    study = _study(level3={"x": 1}, deposit_inspection={"y": 1}, classification_result={"z": 1}, assessment={"a": 1})
    study.analysis_run_id = 7
    plan = _plan(_record(), finding_claim={"kind": "gene"})
    moved = invalidate(study, plan, {"contrast"}, revision=1)
    assert set(moved) == {"level3", "deposit_inspection", "classification_result", "finding_claim", "analysis_run_id"}
    assert "level3" not in study.evidence_json
    assert study.evidence_json["assessment"] == {"a": 1}  # not revision-scoped
    assert plan.finding_claim_json is None and study.analysis_run_id is None
    [entry] = study.evidence_json["selection_history"]
    assert entry["revision"] == 1 and entry["artifacts"]["level3"] == {"x": 1}


def test_a_changed_sample_mapping_keeps_the_inspection_and_the_author_results():
    study = _study(deposit_inspection={"y": 1}, deposit_metadata_association={"m": 1}, level3={"x": 1})
    plan = _plan(_record(), finding_claim={"kind": "gene"})
    moved = invalidate(study, plan, {"sample_mapping"}, revision=1)
    assert "deposit_inspection" in study.evidence_json
    assert plan.finding_claim_json == {"kind": "gene"}
    assert set(moved) == {"deposit_metadata_association", "level3"}


def test_artifacts_are_stamped_with_the_revision_they_were_computed_for_and_stale_ones_leave():
    study = _study(level3={"x": 1})
    plan = _plan(_record())
    sync_revisions(study, plan)
    assert study.evidence_json["artifact_revisions"] == {"level3": 1}
    plan.analysis_selection_json, _ = revise(plan.analysis_selection_json, {"predicate": {"kind": "padj"}}, decided_by="human", reason="r")
    assert stale_artifacts(study, plan) == ["level3"]
    sync_revisions(study, plan)
    assert "level3" not in study.evidence_json
    assert study.evidence_json["selection_history"][0]["artifacts"] == {"level3": {"x": 1}}


def test_a_plan_with_no_selection_is_left_alone():
    study = _study(level3={"x": 1})
    sync_revisions(study, _plan(None))
    assert "artifact_revisions" not in study.evidence_json


# ---- a person's choice at the gate is a new revision ----


_P = [{"kind": "pvalue", "operator": "<", "value": 0.01}]


async def _gate(session, admin_user):
    from app.services.reproduction_plan_service import ReproductionPlanService
    from app.services.validation_study_service import ValidationStudyService

    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    experiments = [
        {"id": "e1", "assay": "bulk RNA-seq", "status": "extracted", "workflow": "nf-core/rnaseq", "workflow_version": "3.14.0"},
        {"id": "e2", "assay": "ATAC-seq", "status": "extracted", "workflow": "nf-core/atacseq", "workflow_version": "2.1.2"},
    ]
    design = {
        "contrasts": [
            {"name": "KO vs WT", "assay": "bulk RNA-seq", "test_samples": [], "reference_samples": [], "cutoffs": _P},
            {"name": "KO vs WT accessibility", "assay": "ATAC-seq", "test_samples": [], "reference_samples": [], "cutoffs": _P},
        ],
        "selected_contrast": {"contrast_index": 0, "decided_by": "claim_selection", "reason": "r", "confidence": 0.8, "model": "m"},
    }
    record = {
        "current": {"revision": 1, "reported_experiment_id": "e1", "claim_index": 0, "check": "processed_reanalysis",
                    "contrast_index": 0, "workflow": "nf-core/rnaseq", "decided_by": "model", "reason": "r"},
        "history": [],
        "candidates": [{"claim_index": 0, "check": "processed_reanalysis"}, {"claim_index": 1, "check": "raw_reanalysis"}],
        "unassessed": [{"claim_index": 1, "reason": "not selected"}],
    }
    plan = await ReproductionPlanService.create_plan(
        session, study, admin_user.id, pipeline_key="nf-core/rnaseq", differential_design=design,
        reported_experiments=experiments, analysis_selection=record,
    )
    await ReproductionPlanService.add_comparison_targets(
        session,
        plan,
        [
            {"metric_key": "", "claim_text": "257 genes up", "claimed_value": 257, "contrast_index": 0, "reported_experiment_id": "e1"},
            {"metric_key": "", "claim_text": "900 regions opened", "claimed_value": 900, "contrast_index": 1, "reported_experiment_id": "e2"},
        ],
    )
    for st in ("acquiring_text", "reading", "plan_ready"):
        await ValidationStudyService.transition(session, study.id, admin_user.organization_id, admin_user.id, st)
    study.evidence_json = {**(study.evidence_json or {}), "level3": {"from": "revision 1"}}
    await session.flush()
    return study, plan


@pytest.mark.asyncio
async def test_choosing_another_contrast_at_the_gate_revises_the_selection_and_its_workflow(session, admin_user):
    from app.services.reproduction_plan_service import ReproductionPlanService

    study, plan = await _gate(session, admin_user)
    edited = {
        "contrasts": [
            {"name": "KO vs WT accessibility", "test_samples": ["K1", "K2"], "reference_samples": ["W1", "W2"]}
        ]
    }
    plan = await ReproductionPlanService.set_differential_design(
        session, study.id, admin_user.organization_id, admin_user.id, edited, selected_contrast_index=1
    )
    current = plan.analysis_selection_json["current"]
    assert (current["revision"], current["claim_index"], current["contrast_index"]) == (2, 1, 1)
    assert current["decided_by"] == "human"
    assert current["check"] == "raw_reanalysis"
    assert current["reported_experiment_id"] == "e2"
    assert plan.pipeline_key == "nf-core/atacseq"
    assert plan.analysis_selection_json["history"][0]["revision"] == 1
    assert "level3" not in study.evidence_json
    assert study.evidence_json["selection_history"][0]["artifacts"]["level3"] == {"from": "revision 1"}


@pytest.mark.asyncio
async def test_saving_the_selected_contrast_again_is_not_a_new_revision(session, admin_user):
    from app.services.reproduction_plan_service import ReproductionPlanService

    study, plan = await _gate(session, admin_user)
    edited = {"contrasts": [{"name": "KO vs WT", "test_samples": ["K1", "K2"], "reference_samples": ["W1", "W2"]}]}
    plan = await ReproductionPlanService.set_differential_design(
        session, study.id, admin_user.organization_id, admin_user.id, edited, selected_contrast_index=0
    )
    assert plan.analysis_selection_json["current"]["revision"] == 1
    assert study.evidence_json["level3"] == {"from": "revision 1"}


# ---- "Review and resume" ----


async def _classified_legacy_study(session, admin_user, *, ambiguous=True):
    from app.services.reproduction_plan_service import ReproductionPlanService
    from app.services.validation_study_service import ValidationStudyService

    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    study.intended_route = "deposit"
    await session.flush()
    contrasts = [
        {"name": "KO vs WT", "assay": "bulk RNA-seq", "test_samples": ["K1", "K2"], "reference_samples": ["W1", "W2"]},
        {"name": "KO vs WT binding", "assay": "ChIP-seq" if ambiguous else "RNA-seq", "test_samples": [], "reference_samples": []},
    ]
    await ReproductionPlanService.create_plan(
        session, study, admin_user.id, pipeline_key="nf-core/rnaseq",
        differential_design={"contrasts": contrasts, "selected_contrast": {"contrast_index": 0, "decided_by": "model"}},
    )
    study.state = "classified"
    study.classification = "inconclusive"
    await session.flush()
    return study


@pytest.mark.asyncio
async def test_a_resumed_ambiguous_legacy_plan_waits_for_a_renewed_selection(session, admin_user):
    from fastapi import HTTPException

    from app.services.reproduction_plan_service import ReproductionPlanService
    from app.services.validation_driver_service import ValidationDriverService, is_advancing
    from app.services.validation_study_service import ValidationStudyService

    study = await _classified_legacy_study(session, admin_user)
    study = await ValidationStudyService.resume_study(session, study.id, admin_user.organization_id, admin_user.id)
    assert study.state == "plan_ready"
    assert study.evidence_json["awaiting_renewed_selection"]
    assert is_advancing(study) is False

    # The driver holds rather than approving or concluding.
    assert await ValidationDriverService._handle_plan_ready(session, study) is False
    assert study.state == "plan_ready"
    with pytest.raises(HTTPException) as refused:
        await ValidationStudyService.approve_plan(session, study.id, admin_user.organization_id, admin_user.id)
    assert "Choose" in refused.value.detail

    # Confirming a contrast at the gate is the renewed selection, even the same one.
    edited = {"contrasts": [{"name": "KO vs WT", "test_samples": ["K1", "K2"], "reference_samples": ["W1", "W2"]}]}
    plan = await ReproductionPlanService.set_differential_design(
        session, study.id, admin_user.organization_id, admin_user.id, edited, selected_contrast_index=0
    )
    assert plan.analysis_selection_json["current"]["decided_by"] == "human"
    assert "awaiting_renewed_selection" not in study.evidence_json


@pytest.mark.asyncio
async def test_a_resumed_unambiguous_legacy_plan_needs_no_renewal(session, admin_user):
    from app.services.validation_study_service import ValidationStudyService

    study = await _classified_legacy_study(session, admin_user, ambiguous=False)
    study = await ValidationStudyService.resume_study(session, study.id, admin_user.organization_id, admin_user.id)
    assert "awaiting_renewed_selection" not in (study.evidence_json or {})


@pytest.mark.asyncio
async def test_every_driver_tick_stamps_what_it_computed_with_the_current_revision(session, admin_user):
    from app.services.validation_driver_service import ValidationDriverService

    study, plan = await _gate(session, admin_user)
    study.state = "running"  # no run: the handler fails the study, and the tick still stamps
    await session.flush()
    await ValidationDriverService._advance_one(session, study)
    assert study.evidence_json["artifact_revisions"] == {"level3": 1}
