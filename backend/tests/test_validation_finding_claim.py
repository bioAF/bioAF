"""B4 (ADR-069 / spec-08): the human confirms the paper's ground-truth result set at the C1 gate.

The scientist supplies the paper's deposited differential result table (DEG list / DA peak list);
the service normalizes it into a directional FindingSet and persists it as the plan's finding claim,
which Level-3 concordance later scores our reproduction against. Auto-fetch is assist; the
human-supplied confirm is the backbone (spike-03). Persisting the claim is what lets plan approval
populate evidence["level3"].paper_finding_set and run the reproducing state.
"""

import pytest

from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_study_service import ValidationStudyService

# A minimal DESeq2-style DEG table (gene symbols). Defaults |lfc|>=1 & padj<=0.05 -> 2 significant.
_DE_TABLE = (
    "gene,log2FoldChange,padj\n"
    "A1BG,2.5,0.001\n"  # up, significant
    "TP53,-1.8,0.01\n"  # down, significant
    "GAPDH,0.2,0.9\n"  # not significant
    "MYC,1.2,0.2\n"  # lfc passes but padj does not
)

# A differential-peak table (ATAC/ChIP DA). Interval entities keyed chrom:start-end.
_DA_TABLE = (
    "chr\tstart\tend\tlog2fc\tpadj\n"
    "chr1\t1000\t2000\t2.0\t0.001\n"
    "chr2\t5000\t6000\t-1.5\t0.02\n"
    "chr3\t100\t200\t0.1\t0.5\n"  # not significant
)


async def _plan_ready_study(session, admin_user, design=None):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    plan = await ReproductionPlanService.create_plan(
        session, study, admin_user.id, pipeline_key="nf-core/rnaseq", differential_design=design
    )
    for st in ("acquiring_text", "reading", "plan_ready"):
        await ValidationStudyService.transition(session, study.id, admin_user.organization_id, admin_user.id, st)
    await session.flush()
    return study, plan


@pytest.mark.asyncio
async def test_set_finding_claim_normalizes_and_persists_gene_set(session, admin_user):
    study, plan = await _plan_ready_study(session, admin_user)
    claim = await ReproductionPlanService.set_finding_claim(
        session,
        study.id,
        admin_user.organization_id,
        admin_user.id,
        kind="gene",
        table_text=_DE_TABLE,
        source_locator="Table S3",
    )
    await session.commit()

    assert claim["kind"] == "gene"
    assert claim["confirmed"] is True
    assert claim["source_locator"] == "Table S3"
    fs = claim["finding_set"]
    assert fs["n_sig"] == 2
    assert {e["id"]: e["direction"] for e in fs["entities"]} == {"A1BG": "up", "TP53": "down"}
    # Persisted on the plan so approval can read it into evidence["level3"].
    assert plan.finding_claim_json["finding_set"]["n_sig"] == 2
    assert plan.finding_claim_json["namespace"] == "symbol"


@pytest.mark.asyncio
async def test_set_finding_claim_normalizes_interval_set(session, admin_user):
    study, plan = await _plan_ready_study(session, admin_user)
    claim = await ReproductionPlanService.set_finding_claim(
        session, study.id, admin_user.organization_id, admin_user.id, kind="interval", table_text=_DA_TABLE
    )
    await session.commit()
    fs = claim["finding_set"]
    assert claim["kind"] == "interval"
    assert fs["n_sig"] == 2
    assert {e["id"] for e in fs["entities"]} == {"chr1:1000-2000", "chr2:5000-6000"}


@pytest.mark.asyncio
async def test_set_finding_claim_defaults_thresholds_from_plan_design(session, admin_user):
    # The paper's stated thresholds (captured in B2e) are the ones used to normalize its own table.
    design = {"contrasts": [{"name": "x"}], "thresholds": {"log2fc": 2.0, "padj": 0.05}}
    study, plan = await _plan_ready_study(session, admin_user, design=design)
    claim = await ReproductionPlanService.set_finding_claim(
        session, study.id, admin_user.organization_id, admin_user.id, kind="gene", table_text=_DE_TABLE
    )
    # With |lfc|>=2.0 only A1BG (2.5) qualifies; TP53 (-1.8) drops below the paper's own cutoff.
    assert {e["id"] for e in claim["finding_set"]["entities"]} == {"A1BG"}
    assert claim["thresholds"] == {"log2fc": 2.0, "padj": 0.05}


@pytest.mark.asyncio
async def test_set_finding_claim_rejected_when_not_plan_ready(session, admin_user):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    await ReproductionPlanService.create_plan(session, study, admin_user.id, pipeline_key="nf-core/rnaseq")
    await session.flush()
    # Still in 'requested', not at the C1 gate: confirming a ground-truth set is out of order.
    with pytest.raises(Exception):
        await ReproductionPlanService.set_finding_claim(
            session, study.id, admin_user.organization_id, admin_user.id, kind="gene", table_text=_DE_TABLE
        )


@pytest.mark.asyncio
async def test_set_finding_claim_is_org_scoped(session, admin_user):
    study, _ = await _plan_ready_study(session, admin_user)
    with pytest.raises(Exception):
        await ReproductionPlanService.set_finding_claim(
            session,
            study.id,
            admin_user.organization_id + 999,
            admin_user.id,
            kind="gene",
            table_text=_DE_TABLE,
        )
