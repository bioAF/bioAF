"""The C1 gate asks for help when a deposited table's columns are not recognised.

`assisted` hands the header to a person; `autonomous` asks the model. Either way the alternative it
replaces is a study that reported "could not locate chrom/start/end columns" about a perfectly
standard csaw deposit.
"""

import pytest

from app.services import reproduction_plan_service as rps
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_study_service import ValidationStudyService

pytestmark = pytest.mark.asyncio

_CSAW = (
    '"","regions.seqnames","regions.start","regions.end","combined.FDR","combined.rep.logFC"\n'
    '"1","1",3361051,3361210,0.99,-0.116\n'
    '"2","2",4857701,4857760,0.004,2.31\n'
    '"3","3",5000000,5000500,0.001,-3.02\n'
)
_PLAIN = "chrom,start,end,padj,log2FoldChange\n1,3361051,3361210,0.99,-0.116\n2,4857701,4857760,0.004,2.31\n"


async def _plan_ready_study(session, user, autonomy="assisted"):
    from sqlalchemy import select

    from app.models.organization import Organization

    org = (await session.execute(select(Organization).where(Organization.id == user.organization_id))).scalar_one()
    org.lit_validation_autonomy = autonomy
    study = await ValidationStudyService.create_study(session, user.organization_id, user.id, source_doi="10.1/x")
    await ReproductionPlanService.create_plan(
        session, study, user.id, accessions=["GSE1"], pipeline_key="nf-core/chipseq"
    )
    study.state = "plan_ready"
    await session.flush()
    return study


def _patch_model(monkeypatch, columns, model="claude-opus-4-8"):
    from types import SimpleNamespace

    async def fake_get_for_feature(sess, org_id, feature):
        return SimpleNamespace(provider="anthropic", model=model, api_key=None)

    async def fake_resolve(header, *, kind, client, model, api_key):
        return {"columns": columns, "reason": "csaw prefixes its columns", "confidence": 0.96, "model": model}

    monkeypatch.setattr(rps.llm_provider_config_service, "get_for_feature", fake_get_for_feature)
    monkeypatch.setattr(rps, "get_client", lambda p: object())
    monkeypatch.setattr(rps, "resolve_columns", fake_resolve)


async def test_a_recognised_table_never_asks_for_help(session, admin_user, monkeypatch):
    """The path that already works must not pay for a model call or change its answer."""
    study = await _plan_ready_study(session, admin_user, autonomy="autonomous")

    def _boom(*a, **k):
        raise AssertionError("must not ask for help about a table that parsed")

    monkeypatch.setattr(rps, "resolve_columns", _boom)
    claim = await ReproductionPlanService.set_finding_claim(
        session, study.id, admin_user.organization_id, admin_user.id, kind="interval", table_text=_PLAIN
    )
    assert len(claim["finding_set"]["entities"]) == 1
    assert claim.get("needs_column_mapping") is None


async def test_assisted_hands_the_header_to_a_person(session, admin_user):
    """No model call in assisted mode. The gate gets the header and the roles still to fill."""
    study = await _plan_ready_study(session, admin_user, autonomy="assisted")

    claim = await ReproductionPlanService.set_finding_claim(
        session, study.id, admin_user.organization_id, admin_user.id, kind="interval", table_text=_CSAW
    )
    ask = claim["needs_column_mapping"]
    assert "regions.seqnames" in ask["header"]
    assert set(ask["roles"]) >= {"chrom", "start", "end"}
    assert claim["finding_set"]["entities"] == []


async def test_autonomous_asks_the_model_and_reparses(session, admin_user, monkeypatch):
    """The whole point: the same table parses, and what the model chose is on the record."""
    study = await _plan_ready_study(session, admin_user, autonomy="autonomous")
    _patch_model(
        monkeypatch,
        {
            "chrom": "regions.seqnames",
            "start": "regions.start",
            "end": "regions.end",
            "lfc": "combined.rep.logFC",
            "padj": "combined.FDR",
        },
    )

    claim = await ReproductionPlanService.set_finding_claim(
        session, study.id, admin_user.organization_id, admin_user.id, kind="interval", table_text=_CSAW
    )
    assert [e["id"] for e in claim["finding_set"]["entities"]] == ["2:4857701-4857760", "3:5000000-5000500"]
    decided = claim["column_mapping"]
    assert decided["columns"]["chrom"] == "regions.seqnames"
    assert decided["decided_by"] == "model"
    assert decided["model"] == "claude-opus-4-8"
    assert decided["confidence"] == 0.96
    assert claim.get("needs_column_mapping") is None


async def test_a_caller_supplied_map_is_used_without_asking_anyone(session, admin_user, monkeypatch):
    """What the assisted picker posts back. No model call, and the mapping is recorded as the human's."""
    study = await _plan_ready_study(session, admin_user, autonomy="assisted")

    def _boom(*a, **k):
        raise AssertionError("a supplied map must not trigger a model call")

    monkeypatch.setattr(rps, "resolve_columns", _boom)
    claim = await ReproductionPlanService.set_finding_claim(
        session,
        study.id,
        admin_user.organization_id,
        admin_user.id,
        kind="interval",
        table_text=_CSAW,
        column_map={
            "chrom": "regions.seqnames",
            "start": "regions.start",
            "end": "regions.end",
            "lfc": "combined.rep.logFC",
            "padj": "combined.FDR",
        },
    )
    assert len(claim["finding_set"]["entities"]) == 2
    assert claim["column_mapping"]["decided_by"] == "human"


async def test_autonomous_that_gets_no_answer_falls_back_to_asking_a_person(session, admin_user, monkeypatch):
    """A provider outage must not look like an unusable deposit."""
    study = await _plan_ready_study(session, admin_user, autonomy="autonomous")

    async def fake_resolve(header, *, kind, client, model, api_key):
        return None

    from types import SimpleNamespace

    async def fake_get_for_feature(sess, org_id, feature):
        return SimpleNamespace(provider="anthropic", model="m", api_key=None)

    monkeypatch.setattr(rps.llm_provider_config_service, "get_for_feature", fake_get_for_feature)
    monkeypatch.setattr(rps, "get_client", lambda p: object())
    monkeypatch.setattr(rps, "resolve_columns", fake_resolve)

    claim = await ReproductionPlanService.set_finding_claim(
        session, study.id, admin_user.organization_id, admin_user.id, kind="interval", table_text=_CSAW
    )
    assert claim["needs_column_mapping"]["header"]


async def test_the_selected_contrasts_thresholds_normalize_the_table(session, admin_user):
    """A DEG cutoff applied to a windowed binding table is the wrong number. The contrast this run
    reproduces owns the cutoffs, and the paper-level pair is only the fallback."""
    study = await _plan_ready_study(session, admin_user)
    plan = await ReproductionPlanService.get_plan(session, study.id, admin_user.organization_id)
    plan.differential_design_json = {
        "contrasts": [
            {"name": "DEGs (RNA-seq)", "thresholds": {"log2fc": 1.0, "padj": 0.05}},
            {"name": "binding (ChIP-seq)", "thresholds": {"log2fc": None, "padj": 0.05}},
        ],
        "thresholds": {"log2fc": 1.0, "padj": 0.05},
        "selected_contrast": {"contrast_index": 1, "decided_by": "model", "reason": "r", "confidence": 0.9},
    }
    await session.flush()

    claim = await ReproductionPlanService.set_finding_claim(
        session,
        study.id,
        admin_user.organization_id,
        admin_user.id,
        kind="interval",
        table_text=(
            "chrom,start,end,padj,log2FoldChange\n"
            "1,100,200,0.01,0.4\n"  # significant, small effect: kept on FDR alone, cut by |lfc|>=1
            "2,300,400,0.01,2.5\n"
        ),
    )
    assert claim["thresholds"] == {"log2fc": 0.0, "padj": 0.05}
    assert len(claim["finding_set"]["entities"]) == 2


async def test_a_plan_with_no_selection_still_uses_the_paper_level_pair(session, admin_user):
    """Every plan written before selection existed, study 6 included."""
    study = await _plan_ready_study(session, admin_user)
    plan = await ReproductionPlanService.get_plan(session, study.id, admin_user.organization_id)
    plan.differential_design_json = {"contrasts": [{"name": "a"}], "thresholds": {"log2fc": 1.0, "padj": 0.05}}
    await session.flush()

    claim = await ReproductionPlanService.set_finding_claim(
        session,
        study.id,
        admin_user.organization_id,
        admin_user.id,
        kind="interval",
        table_text="chrom,start,end,padj,log2FoldChange\n1,100,200,0.01,0.4\n2,300,400,0.01,2.5\n",
    )
    assert claim["thresholds"] == {"log2fc": 1.0, "padj": 0.05}
    assert len(claim["finding_set"]["entities"]) == 1
