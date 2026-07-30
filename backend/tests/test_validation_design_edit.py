"""B2e design edit at the C1 gate (lit_validation Level-3).

The extractor drafts the differential design, but its sample labels rarely match the analysis
matrix's column names (e.g. salmon emits SRX ids). So the human ratifies/edits the design at the C1
gate before Level-3 runs it. This covers the service that persists an edited design onto the plan,
guarded to plan_ready and org-scoped, normalized to the canonical shape.
"""

import pytest

from app.services.reproduction_plan_service import ReproductionPlanService, validate_paired_designs
from app.services.validation_study_service import ValidationStudyService


def _paired(subjects):
    return {
        "contrasts": [
            {
                "name": "mucoderm vs tcs",
                "test_samples": ["T1", "T2"],
                "reference_samples": ["R1", "R2"],
                "subjects": subjects,
            }
        ],
        "thresholds": {"log2fc": 1.0, "padj": 0.05},
    }


# --- pure validator (no DB) ---


def test_validate_paired_designs_accepts_balanced_pairing():
    # donorA and donorB each contribute one sample to BOTH arms: a proper paired design.
    design = _paired({"T1": "donorA", "T2": "donorB", "R1": "donorA", "R2": "donorB"})
    assert validate_paired_designs(design) == []


def test_validate_paired_designs_ignores_contrasts_without_subjects():
    design = {"contrasts": [{"test_samples": ["T1"], "reference_samples": ["R1"]}], "thresholds": {}}
    assert validate_paired_designs(design) == []


def test_validate_paired_designs_flags_unlabeled_sample():
    design = _paired({"T1": "donorA", "R1": "donorA", "R2": "donorB"})  # T2 has no label
    errs = validate_paired_designs(design)
    assert any("T2" in e for e in errs)


def test_validate_paired_designs_flags_single_subject():
    design = _paired({"T1": "donorA", "T2": "donorA", "R1": "donorA", "R2": "donorA"})
    errs = validate_paired_designs(design)
    assert any("2" in e for e in errs)  # needs >= 2 distinct subjects


def test_validate_paired_designs_flags_confounded_subject():
    # donorB appears only in the reference arm -> confounded with condition (DESeq2: not full rank).
    design = _paired({"T1": "donorA", "T2": "donorA", "R1": "donorA", "R2": "donorB"})
    errs = validate_paired_designs(design)
    assert any("donorB" in e for e in errs)


def test_validate_paired_designs_flags_stray_label():
    design = _paired({"T1": "donorA", "T2": "donorB", "R1": "donorA", "R2": "donorB", "GHOST": "donorC"})
    errs = validate_paired_designs(design)
    assert any("GHOST" in e for e in errs)


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

    dd = saved.differential_design_json
    assert dd is not None
    assert dd["thresholds"] == {"log2fc": 1.0, "padj": 0.05}
    c = dd["contrasts"][0]
    assert c["test_samples"] == ["SRX1", "SRX2"]
    # Normalized to the canonical shape: missing sub-fields become explicit None, not absent keys.
    assert c["reference_condition"] == "untreated"


@pytest.mark.asyncio
async def test_set_differential_design_persists_a_balanced_pairing(session, admin_user):
    study, plan = await _plan_ready_study(session, admin_user)
    design = _paired({"T1": "donorA", "T2": "donorB", "R1": "donorA", "R2": "donorB"})
    saved = await ReproductionPlanService.set_differential_design(
        session, study.id, admin_user.organization_id, admin_user.id, design
    )
    await session.commit()
    dd = saved.differential_design_json
    assert dd is not None
    assert dd["contrasts"][0]["subjects"] == {
        "T1": "donorA",
        "T2": "donorB",
        "R1": "donorA",
        "R2": "donorB",
    }


@pytest.mark.asyncio
async def test_set_differential_design_rejects_a_confounded_pairing(session, admin_user):
    study, plan = await _plan_ready_study(session, admin_user)
    design = _paired({"T1": "donorA", "T2": "donorA", "R1": "donorA", "R2": "donorB"})
    with pytest.raises(Exception):
        await ReproductionPlanService.set_differential_design(
            session, study.id, admin_user.organization_id, admin_user.id, design
        )


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
