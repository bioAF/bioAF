"""ReproductionPlan + ComparisonTarget persistence (lit_validation B2/B3 output, C1 input).

The plan is the structured, reviewable output of "read the paper": accessions, derived sample
sheet, the chosen nf-core pipeline mapping, and the quantitative claims to check. It is what the
human ratifies at the C1 gate.
"""

import pytest
from sqlalchemy import select

from app.models.comparison_target import ComparisonTarget
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_study_service import ValidationStudyService


async def _study(session, admin_user):
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_accession="GSE52778"
    )
    await session.flush()
    return study


@pytest.mark.asyncio
async def test_create_plan_links_to_study_and_records_mapping(session, admin_user):
    study = await _study(session, admin_user)
    plan = await ReproductionPlanService.create_plan(
        session,
        study,
        admin_user.id,
        accessions=["GSE52778"],
        sample_sheet={"organism": "Homo sapiens", "layout": "PAIRED"},
        pipeline_key="nf-core/rnaseq",
        pipeline_version="3.14.0",
        parameters={"aligner": "star_salmon"},
        reference_genome="GRCh38",
        mapping_confidence="partial",
        mapping_notes="legacy TopHat/Cufflinks mapped to nf-core/rnaseq",
        blockers=[],
        extractor_model="claude-opus-4-8",
        extractor_provider="anthropic",
    )
    await session.commit()

    assert plan.id is not None
    assert plan.validation_study_id == study.id
    assert plan.accessions_json == ["GSE52778"]
    assert plan.pipeline_key == "nf-core/rnaseq"
    assert plan.mapping_confidence == "partial"
    # The study points back at its current plan.
    assert study.reproduction_plan_id == plan.id


@pytest.mark.asyncio
async def test_add_comparison_targets_links_to_plan(session, admin_user):
    study = await _study(session, admin_user)
    plan = await ReproductionPlanService.create_plan(
        session, study, admin_user.id, accessions=["GSE52778"], pipeline_key="nf-core/rnaseq"
    )
    targets = await ReproductionPlanService.add_comparison_targets(
        session,
        plan,
        [
            {
                "metric_key": "alignment_rate",
                "claimed_value": 83.4,
                "unit": "%",
                "tolerance": 0.05,
                "source_locator": "Results, para 2",
            },
            {"metric_key": "de_genes", "claimed_value": 316, "unit": "count", "source_locator": "Fig 3"},
        ],
    )
    await session.commit()

    assert len(targets) == 2
    rows = list(
        (
            await session.execute(select(ComparisonTarget).where(ComparisonTarget.reproduction_plan_id == plan.id))
        ).scalars()
    )
    assert {t.metric_key for t in rows} == {"alignment_rate", "de_genes"}
    align = next(t for t in rows if t.metric_key == "alignment_rate")
    assert align.claimed_value == 83.4
    assert align.tolerance == 0.05


@pytest.mark.asyncio
async def test_get_plan_is_org_scoped(session, admin_user):
    study = await _study(session, admin_user)
    plan = await ReproductionPlanService.create_plan(
        session, study, admin_user.id, accessions=["GSE52778"], pipeline_key="nf-core/rnaseq"
    )
    await session.commit()

    got = await ReproductionPlanService.get_plan(session, study.id, admin_user.organization_id)
    assert got is not None and got.id == plan.id

    wrong_org = await ReproductionPlanService.get_plan(session, study.id, admin_user.organization_id + 999)
    assert wrong_org is None


@pytest.mark.asyncio
async def test_create_plan_records_blockers_for_unmappable_method(session, admin_user):
    study = await _study(session, admin_user)
    plan = await ReproductionPlanService.create_plan(
        session,
        study,
        admin_user.id,
        accessions=[],
        pipeline_key=None,
        mapping_confidence="none",
        blockers=["no nf-core equivalent for 10x Flex chemistry"],
    )
    await session.commit()
    assert plan.pipeline_key is None
    assert plan.blockers_json == ["no nf-core equivalent for 10x Flex chemistry"]


# ---- a paper's own words cannot 500 the read (found by plan_4's final verification, 2026-08-30) ----
#
# Re-planning 10.1038/s41598-023-33729-4 against the live demo raised
# StringDataRightTruncationError and rolled the whole extraction back, leaving the study unplannable.
# The extractor had faithfully captured a claim whose unit is "genes (NOTCH4, JAG1, LIFR, CCNA2,
# CCND2, RB1, SMAD4, JUND, CREBBP)", 66 characters into a 50-character column. The values here come
# from a model reading a methods section, so their length is not something bioAF gets to assume.


@pytest.mark.asyncio
async def test_a_long_unit_is_kept_not_crashed_on(session, admin_user):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    plan = await ReproductionPlanService.create_plan(session, study, admin_user.id)

    unit = "genes (NOTCH4, JAG1, LIFR, CCNA2, CCND2, RB1, SMAD4, JUND, CREBBP)"
    targets = await ReproductionPlanService.add_comparison_targets(
        session,
        plan,
        [{"metric_key": "genes_increased_methylation_by_both_agents", "claimed_value": 9.0, "unit": unit}],
    )
    await session.commit()

    assert targets[0].unit == unit


@pytest.mark.asyncio
async def test_a_value_longer_than_its_column_is_clamped_rather_than_raised(session, admin_user):
    """Widening a column answers today's paper. Clamping answers the next one: an extraction that
    raises rolls back the whole plan, so a study whose paper wrote a long enough phrase cannot be
    planned at all, and the failure is a 500 rather than anything a scientist can act on."""
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    plan = await ReproductionPlanService.create_plan(session, study, admin_user.id)

    targets = await ReproductionPlanService.add_comparison_targets(
        session,
        plan,
        [
            {
                "metric_key": "k" * 400,
                "claimed_value": 1.0,
                "unit": "u" * 400,
                "source_locator": "s" * 400,
            }
        ],
    )
    await session.commit()

    assert len(targets[0].metric_key) <= 100
    assert len(targets[0].unit) <= 255
    assert len(targets[0].source_locator) <= 255
