"""B2e design edit at the C1 gate (lit_validation Level-3).

The extractor drafts the differential design, but its sample labels rarely match the analysis
matrix's column names (e.g. salmon emits SRX ids). So the human ratifies/edits the design at the C1
gate before Level-3 runs it. This covers the service that persists an edited design onto the plan,
guarded to plan_ready and org-scoped, normalized to the canonical shape.
"""

import pytest

from app.services.reproduction_plan_service import (
    ReproductionPlanService,
    validate_paired_designs,
    validate_replicates,
)
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


# --- replicate guard (DESeq2 needs within-group variance) ---


def _arms(test, reference):
    return {
        "contrasts": [{"name": "dex vs untreated", "test_samples": test, "reference_samples": reference}],
        "thresholds": {"log2fc": 1.0, "padj": 0.05},
    }


def test_validate_replicates_accepts_two_per_arm():
    assert validate_replicates(_arms(["T1", "T2"], ["R1", "R2"])) == []


def test_validate_replicates_rejects_one_versus_one():
    """DESeq2 estimates dispersion from within-group variance; one sample per arm leaves none. This is
    the failure an scRNA-seq study hits first, because pseudobulk makes the SAMPLE count the replicate
    count and published scRNA designs routinely run 2-4 samples per arm."""
    errs = validate_replicates(_arms(["T1"], ["R1"]))
    assert len(errs) == 2
    joined = " ".join(errs)
    assert "test" in joined and "reference" in joined
    assert "1" in joined  # the offending count is named


def test_validate_replicates_names_only_the_offending_arm():
    errs = validate_replicates(_arms(["T1", "T2", "T3"], ["R1"]))
    assert len(errs) == 1
    assert "reference" in errs[0]
    assert "T1" not in errs[0]


def test_validate_replicates_rejects_an_arm_with_no_samples():
    errs = validate_replicates(_arms([], ["R1", "R2"]))
    assert len(errs) == 1
    assert "test" in errs[0]


def test_validate_replicates_ignores_an_empty_contrast_list():
    assert validate_replicates({"contrasts": [], "thresholds": {}}) == []


@pytest.mark.asyncio
async def test_set_differential_design_rejects_one_sample_per_arm(session, admin_user):
    study, plan = await _plan_ready_study(session, admin_user)
    with pytest.raises(Exception) as exc:
        await ReproductionPlanService.set_differential_design(
            session, study.id, admin_user.organization_id, admin_user.id, _arms(["T1"], ["R1"])
        )
    assert "test" in str(exc.value)


@pytest.mark.asyncio
async def test_set_differential_design_rejects_a_single_reference_sample(session, admin_user):
    study, plan = await _plan_ready_study(session, admin_user)
    with pytest.raises(Exception) as exc:
        await ReproductionPlanService.set_differential_design(
            session, study.id, admin_user.organization_id, admin_user.id, _arms(["T1", "T2", "T3"], ["R1"])
        )
    assert "reference" in str(exc.value)


@pytest.mark.asyncio
async def test_set_differential_design_accepts_two_per_arm(session, admin_user):
    study, plan = await _plan_ready_study(session, admin_user)
    saved = await ReproductionPlanService.set_differential_design(
        session, study.id, admin_user.organization_id, admin_user.id, _arms(["T1", "T2"], ["R1", "R2"])
    )
    await session.commit()
    design = saved.differential_design_json
    assert design is not None
    assert design["contrasts"][0]["test_samples"] == ["T1", "T2"]


@pytest.mark.asyncio
async def test_set_differential_design_reports_replicate_and_pairing_problems_together(session, admin_user):
    """Two independent guards on one design. A human fixing a rejected design should see everything
    wrong with it in one pass, not discover the second problem after fixing the first."""
    study, plan = await _plan_ready_study(session, admin_user)
    design = {
        "contrasts": [
            {
                "name": "x",
                "test_samples": ["T1"],
                "reference_samples": ["R1"],
                "subjects": {"T1": "donorA", "R1": "donorA"},  # one distinct subject: not a pairing
            }
        ],
        "thresholds": {"log2fc": 1.0, "padj": 0.05},
    }
    with pytest.raises(Exception) as exc:
        await ReproductionPlanService.set_differential_design(
            session, study.id, admin_user.organization_id, admin_user.id, design
        )
    message = str(exc.value)
    assert "test" in message  # the replicate guard fired
    assert "subject" in message  # and so did the paired-design guard
