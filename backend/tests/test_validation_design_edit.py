"""B2e design edit at the C1 gate (lit_validation Level-3).

The extractor drafts the differential design, but its sample labels rarely match the analysis
matrix's column names (e.g. salmon emits SRX ids). So the human ratifies/edits the design at the C1
gate before Level-3 runs it. This covers the service that persists an edited design onto the plan,
guarded to plan_ready and org-scoped, normalized to the canonical shape.
"""

import pytest

from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_study_service import ValidationStudyService


async def _plan_ready_study(session, admin_user):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    plan = await ReproductionPlanService.create_plan(session, study, admin_user.id, pipeline_key="nf-core/rnaseq")
    for st in ("acquiring_text", "reading", "plan_ready"):
        await ValidationStudyService.transition(session, study.id, admin_user.organization_id, admin_user.id, st)
    await session.flush()
    return study, plan


@pytest.mark.asyncio
async def test_set_differential_design_persists_and_normalizes(session, admin_user):
    study, plan = await _plan_ready_study(session, admin_user)
    design = {
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
    saved = await ReproductionPlanService.set_differential_design(
        session, study.id, admin_user.organization_id, admin_user.id, design
    )
    await session.commit()

    assert saved.differential_design_json["thresholds"] == {"log2fc": 1.0, "padj": 0.05}
    c = saved.differential_design_json["contrasts"][0]
    assert c["test_samples"] == ["SRX1", "SRX2"]
    # Normalized to the canonical shape: missing sub-fields become explicit None, not absent keys.
    assert c["reference_condition"] == "untreated"


@pytest.mark.asyncio
async def test_set_differential_design_empty_contrasts_clears_to_none(session, admin_user):
    # The human decides there is no differential finding to reproduce -> the plan stays Level-2.
    study, plan = await _plan_ready_study(session, admin_user)
    saved = await ReproductionPlanService.set_differential_design(
        session, study.id, admin_user.organization_id, admin_user.id, {"contrasts": [], "thresholds": {}}
    )
    await session.commit()
    assert saved.differential_design_json is None


@pytest.mark.asyncio
async def test_set_differential_design_rejected_when_not_plan_ready(session, admin_user):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    await ReproductionPlanService.create_plan(session, study, admin_user.id, pipeline_key="nf-core/rnaseq")
    await session.flush()
    with pytest.raises(Exception):
        await ReproductionPlanService.set_differential_design(
            session, study.id, admin_user.organization_id, admin_user.id, {"contrasts": [{"name": "x"}]}
        )


@pytest.mark.asyncio
async def test_set_differential_design_org_scoped(session, admin_user):
    study, _ = await _plan_ready_study(session, admin_user)
    with pytest.raises(Exception):
        await ReproductionPlanService.set_differential_design(
            session, study.id, admin_user.organization_id + 999, admin_user.id, {"contrasts": [{"name": "x"}]}
        )
