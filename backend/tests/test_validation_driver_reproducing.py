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


@pytest.mark.asyncio
async def test_reproducing_launch_failure_errors(session, admin_user, monkeypatch):
    async def _fake_exec(*a, **k):
        return SimpleNamespace(id=778, status="failed")

    monkeypatch.setattr(NotebookExecutionService, "execute_template", _fake_exec)
    study = await _study_in(session, admin_user, "reproducing", evidence={"level3": {"template_id": 1, "kind": "gene"}})
    await ValidationDriverService._handle_reproducing(session, study)
    assert study.state == "error"


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

    async def _extract(_session, cs, kind):
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
