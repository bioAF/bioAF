"""C3 driver `reproducing` state (lit_validation Level-3, ADR-069).

The reproducing handler launches the headless differential-analysis notebook (G1), polls it, and
on completion scores concordance (E6) before advancing to comparing. When Level-3 inputs are absent
it falls straight through to comparing (Level-2 unchanged). The executor + the output read are
mocked; this pins the driver control flow.
"""

from types import SimpleNamespace

import pytest

from app.services.notebook_execution_service import NotebookExecutionService
from app.services.result_set_normalizer import FindingEntity, FindingSet
from app.services.validation_driver_service import ValidationDriverService
from app.services.validation_study_service import ValidationStudyService


async def _study_in(session, admin_user, state, evidence=None):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    study.state = state
    study.evidence_json = evidence or {}
    await session.flush()
    return study


def _paper_genes():
    return FindingSet(
        kind="gene",
        namespace="symbol",
        entities=[FindingEntity("A", "up"), FindingEntity("B", "up"), FindingEntity("C", "down")],
    )


@pytest.mark.asyncio
async def test_reproducing_skips_to_comparing_without_level3(session, admin_user):
    study = await _study_in(session, admin_user, "reproducing", evidence={"computed_metrics": {}})
    await ValidationDriverService._handle_reproducing(session, study)
    assert study.state == "comparing"


@pytest.mark.asyncio
async def test_reproducing_launches_execution_first_visit(session, admin_user, monkeypatch):
    async def _fake_exec(*a, **k):
        return SimpleNamespace(id=777, status="running")

    monkeypatch.setattr(NotebookExecutionService, "execute_template", _fake_exec)
    study = await _study_in(session, admin_user, "reproducing", evidence={"level3": {"template_id": 1, "kind": "gene"}})
    await ValidationDriverService._handle_reproducing(session, study)
    assert study.state == "reproducing"  # launched; poll next tick
    assert study.evidence_json["level3_run_session_id"] == 777


_LEVEL2_EVIDENCE = {
    "computed_metrics": {"reads_mapped_genome": 0.93},
    "comparison_targets": [{"metric_key": "alignment_rate", "claimed_value": 95.0, "unit": "%"}],
}


def _level3_evidence():
    """A study that earned a Level-2 verdict in `extracting` and then routed to Level-3."""
    return {**_LEVEL2_EVIDENCE, "level3": {"template_id": 1, "kind": "gene"}}


@pytest.mark.asyncio
async def test_reproducing_launch_failure_degrades_to_level2(session, admin_user, monkeypatch):
    """A Level-3 failure must be ADDITIVE, not destructive: the study still owns the Level-2 verdict it
    earned in `extracting`, so a notebook that will not launch degrades to comparing with the reason
    recorded, never to terminal `error` that discards the whole study."""

    async def _fake_exec(*a, **k):
        return SimpleNamespace(id=778, status="failed")

    monkeypatch.setattr(NotebookExecutionService, "execute_template", _fake_exec)
    study = await _study_in(session, admin_user, "reproducing", evidence=_level3_evidence())
    await ValidationDriverService._handle_reproducing(session, study)
    assert study.state == "comparing"
    assert study.evidence_json["level3_failed"]["reason"]
    assert study.failure_reason is None


@pytest.mark.asyncio
async def test_reproducing_midrun_failure_degrades_to_level2(session, admin_user, monkeypatch):
    async def _load(_session, sid):
        return SimpleNamespace(id=sid, status="running")

    async def _poll(_session, cs):
        return SimpleNamespace(status="failed")

    monkeypatch.setattr(ValidationDriverService, "_load_compute_session", _load)
    monkeypatch.setattr(NotebookExecutionService, "poll_execution", _poll)
    study = await _study_in(
        session, admin_user, "reproducing", evidence={**_level3_evidence(), "level3_run_session_id": 777}
    )
    await ValidationDriverService._handle_reproducing(session, study)
    assert study.state == "comparing"
    assert study.evidence_json["level3_failed"]["reason"]


@pytest.mark.asyncio
async def test_reproducing_missing_session_degrades_to_level2(session, admin_user, monkeypatch):
    async def _load(_session, sid):
        return None

    monkeypatch.setattr(ValidationDriverService, "_load_compute_session", _load)
    study = await _study_in(
        session, admin_user, "reproducing", evidence={**_level3_evidence(), "level3_run_session_id": 777}
    )
    await ValidationDriverService._handle_reproducing(session, study)
    assert study.state == "comparing"
    assert study.evidence_json["level3_failed"]["reason"]


@pytest.mark.asyncio
async def test_level2_evidence_survives_a_level3_failure(session, admin_user, monkeypatch):
    """The QC evidence written in `extracting` is what the classifier reads at `comparing`. A Level-3
    failure must leave every bit of it intact, or the degrade silently produces a WORSE Level-2
    verdict than the study would have reached with no Level-3 configured at all."""

    async def _fake_exec(*a, **k):
        return SimpleNamespace(id=779, status="failed")

    monkeypatch.setattr(NotebookExecutionService, "execute_template", _fake_exec)
    study = await _study_in(session, admin_user, "reproducing", evidence=_level3_evidence())
    await ValidationDriverService._handle_reproducing(session, study)
    evidence = study.evidence_json
    assert evidence["computed_metrics"] == _LEVEL2_EVIDENCE["computed_metrics"]
    assert evidence["comparison_targets"] == _LEVEL2_EVIDENCE["comparison_targets"]


@pytest.mark.asyncio
async def test_reproducing_polls_running_stays(session, admin_user, monkeypatch):
    async def _load(_session, sid):
        return SimpleNamespace(id=sid, status="running")

    async def _poll(_session, cs):
        return SimpleNamespace(status="running")

    monkeypatch.setattr(ValidationDriverService, "_load_compute_session", _load)
    monkeypatch.setattr(NotebookExecutionService, "poll_execution", _poll)
    study = await _study_in(
        session,
        admin_user,
        "reproducing",
        evidence={"level3": {"template_id": 1, "kind": "gene"}, "level3_run_session_id": 777},
    )
    advanced = await ValidationDriverService._handle_reproducing(session, study)
    assert advanced is False
    assert study.state == "reproducing"


@pytest.mark.asyncio
async def test_reproducing_completed_scores_concordance_and_advances(session, admin_user, monkeypatch):
    async def _load(_session, sid):
        return SimpleNamespace(id=sid, status="completed")

    async def _poll(_session, cs):
        return SimpleNamespace(status="completed")

    async def _extract(_session, cs, kind, **kwargs):
        # our reproduced set recovers all three of the paper's genes with concordant direction
        return _paper_genes()

    monkeypatch.setattr(ValidationDriverService, "_load_compute_session", _load)
    monkeypatch.setattr(NotebookExecutionService, "poll_execution", _poll)
    monkeypatch.setattr(ValidationDriverService, "_extract_reproduced_set", _extract)

    level3 = {"template_id": 1, "kind": "gene", "paper_finding_set": _paper_genes().to_dict(), "universe": 20000}
    study = await _study_in(
        session, admin_user, "reproducing", evidence={"level3": level3, "level3_run_session_id": 777}
    )
    await ValidationDriverService._handle_reproducing(session, study)
    assert study.state == "comparing"
    conc = study.evidence_json["level3_result"]["concordance"]
    assert conc["verdict"] == "agree"
    assert conc["concordant"] == 3


@pytest.mark.asyncio
async def test_extract_reproduced_set_applies_the_papers_thresholds(session, admin_user, monkeypatch):
    """Our reproduced set must be defined by the SAME cutoffs as the paper's set (the plan's captured
    thresholds), not hardcoded defaults, or a threshold mismatch would spuriously depress overlap."""
    table = "gene,log2FoldChange,padj\nA,2.5,0.001\nB,1.2,0.01\n"

    async def _read(_session, _cs):
        return table

    monkeypatch.setattr(ValidationDriverService, "_read_reproduction_output", _read)
    cs = SimpleNamespace(id=1)

    # Default cutoff |lfc|>=1: both A and B are significant.
    fs_default = await ValidationDriverService._extract_reproduced_set(session, cs, "gene")
    assert {e.id for e in fs_default.entities} == {"A", "B"}

    # The paper's stricter |lfc|>=2 cutoff: only A survives.
    fs_strict = await ValidationDriverService._extract_reproduced_set(
        session, cs, "gene", lfc_threshold=2.0, padj_threshold=0.05
    )
    assert {e.id for e in fs_strict.entities} == {"A"}


async def _comparing_study_with_diverge(session, admin_user, design):
    from app.services.reproduction_plan_service import ReproductionPlanService

    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await ReproductionPlanService.create_plan(
        session,
        study,
        admin_user.id,
        pipeline_key="nf-core/rnaseq",
        mapping_confidence="exact",  # QC side cleared
        reference_genome="GRCh38",
        differential_design=design,
    )
    study.state = "comparing"
    study.evidence_json = {
        "computed_metrics": {},
        "comparison_targets": [],
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
    return study


@pytest.mark.asyncio
async def test_comparing_e3prime_holds_divergence_without_paper_thresholds(session, admin_user):
    """E3' end to end: a concordance divergence with a cleared QC side but NO stated paper thresholds
    (so we cannot claim we applied the paper's cutoffs) is inconclusive, not not_validated."""
    study = await _comparing_study_with_diverge(
        session, admin_user, {"contrasts": [{"name": "x"}], "thresholds": {"log2fc": None, "padj": None}}
    )
    await ValidationDriverService._handle_comparing(session, study)
    assert study.evidence_json["classification_result"]["classification"] == "inconclusive"


@pytest.mark.asyncio
async def test_comparing_e3prime_not_validated_when_thresholds_matched(session, admin_user):
    """With the paper's thresholds stated (and applied) and a comparable method, a cleared divergence
    reaches the strongest negative: not_validated."""
    study = await _comparing_study_with_diverge(
        session, admin_user, {"contrasts": [{"name": "x"}], "thresholds": {"log2fc": 1.0, "padj": 0.05}}
    )
    await ValidationDriverService._handle_comparing(session, study)
    assert study.evidence_json["classification_result"]["classification"] == "not_validated"


@pytest.mark.asyncio
async def test_comparing_folds_in_concordance_verdict(session, admin_user, monkeypatch):
    async def _no_plan(*a, **k):
        return None

    from app.services import validation_driver_service as mod

    monkeypatch.setattr(mod.ReproductionPlanService, "get_plan", _no_plan)
    evidence = {
        "computed_metrics": {},
        "comparison_targets": [],
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
            }
        },
    }
    study = await _study_in(session, admin_user, "comparing", evidence=evidence)
    await ValidationDriverService._handle_comparing(session, study)
    result = study.evidence_json["classification_result"]
    assert result["classification"] == "validated"
    assert result["coverage"]["concordance_agree"] == 1


@pytest.mark.asyncio
async def test_comparing_explains_a_tool_pair_divergence_end_to_end(session, admin_user):
    """The scRNA case the whole of step 5 exists for, at driver level: the paper's finding reproduced,
    and the only diverging number is a cell count that differs because the paper called cells with
    CellRanger and bioAF called them with STARsolo."""
    from app.services.reproduction_plan_service import ReproductionPlanService

    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await ReproductionPlanService.create_plan(
        session,
        study,
        admin_user.id,
        pipeline_key="nf-core/scrnaseq",
        mapping_confidence="exact",
        reference_genome="GRCh38",
        tools=["CellRanger", "Seurat"],
        differential_design={"contrasts": [{"name": "x"}], "thresholds": {"log2fc": 1.0, "padj": 0.05}},
    )
    study.state = "comparing"
    study.evidence_json = {
        "computed_metrics": {"cell_count": 7431},
        "comparison_targets": [{"metric_key": "cell_count", "claimed_value": 10234, "unit": "count"}],
        "level3_result": {
            "concordance": {
                "kind": "gene",
                "verdict": "agree",
                "paper_n": 100,
                "our_n": 95,
                "overlap": 88,
                "concordant": 85,
                "directional_overlap_frac": 0.85,
                "enrichment_p": 1e-30,
                "notes": [],
            }
        },
    }
    await session.flush()

    await ValidationDriverService._handle_comparing(session, study)

    result = study.evidence_json["classification_result"]
    assert result["classification"] == "validated"
    assert result["auto_finalize"] is False
    assert result["divergence_attribution"]["cell_count"]["our_tool"] == "STARsolo"
    assert "CellRanger" in result["reasoning"]
    assert study.state == "comparing"  # held for a human, never auto-finalized
