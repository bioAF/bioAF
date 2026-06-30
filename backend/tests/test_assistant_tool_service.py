"""Tests for the tool catalog (T1) + enforcement wrapper (T2): the ai_pipeline_run keystone.

"Tools enforce, the LLM proposes." Every tool call passes through one wrapper that, before
anything executes, checks the catalog, the caller's RBAC permission for the underlying
action (server-side, never the model's word), and the arguments. Read-only tools execute;
spend tools do NOT execute, they create an ActionPlan and wait for confirmation. Every
outcome is recorded as an AssistantToolInvocation and audited. These tests pin that
decision tree, especially the rejection paths.
"""

import pytest
from sqlalchemy import func, select

from app.models.assistant import AssistantActionPlan, AssistantConversation, AssistantToolInvocation
from app.models.audit_log import AuditLog
from app.models.experiment import Experiment
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.models.pipeline_run import PipelineRun
from app.models.qc_dashboard import QCDashboard
from app.models.sample import Sample
from app.services.assistant_tool_catalog import get_tool
from app.services.assistant_tool_service import AssistantToolService

pytestmark = pytest.mark.asyncio


# ---- Helpers ----


async def _conversation(session, user):
    conv = AssistantConversation(
        organization_id=user.organization_id,
        user_id=user.id,
        title="t",
        provider="anthropic",
        model="claude-opus-4-8",
    )
    session.add(conv)
    await session.flush()
    await session.commit()
    return conv


async def _bulk_mouse_experiment(session, user):
    exp = Experiment(
        organization_id=user.organization_id,
        name="Bulk mouse",
        owner_user_id=user.id,
        status="fastq_uploaded",
    )
    session.add(exp)
    await session.flush()
    session.add(
        Sample(
            experiment_id=exp.id,
            external_id="B1",
            organism="Mus musculus",
            molecule_type="total RNA",
            library_prep_method="TruSeq Stranded mRNA",
        )
    )
    session.add(
        PipelineCatalogEntry(
            organization_id=user.organization_id,
            pipeline_key="nf-core/rnaseq",
            name="nf-core/rnaseq",
            source_type="github",
            version="3.14.0",
            default_params_json={"aligner": "star_salmon"},
            enabled=True,
        )
    )
    await session.flush()
    await session.commit()
    return exp


async def _audit_rows_for(session, tool_invocation_id):
    return (
        await session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.entity_type == "assistant_tool_invocation",
                AuditLog.entity_id == tool_invocation_id,
            )
        )
    ).scalar_one()


# ---- Catalog (T1) ----


async def test_catalog_describes_recommend_pipeline_and_launch_run():
    rec = get_tool("recommend_pipeline")
    assert rec is not None
    assert rec.consequence_class == "read_only"
    assert rec.permission == ("experiments", "view")

    launch = get_tool("launch_run")
    assert launch is not None
    assert launch.consequence_class == "spend"
    # Mirrors the real POST /api/pipeline-runs guard, require_permission("pipelines", "launch").
    assert launch.permission == ("pipelines", "launch")


# ---- Wrapper (T2) ----


async def test_recommend_pipeline_executes_and_is_audited(session, admin_user):
    exp = await _bulk_mouse_experiment(session, admin_user)
    conv = await _conversation(session, admin_user)

    result = await AssistantToolService.invoke(
        session,
        conversation=conv,
        role_id=admin_user.role_id,
        tool_name="recommend_pipeline",
        arguments={"experiment_id": exp.id},
    )

    assert result.status == "succeeded"
    assert result.result["pipeline_key"] == "nf-core/rnaseq"
    assert result.result["reference_genome"] == "GRCm39"

    ti = result.tool_invocation
    assert ti is not None
    assert ti.status == "succeeded"
    assert ti.consequence_class == "read_only"
    assert await _audit_rows_for(session, ti.id) == 1


async def test_unknown_tool_is_rejected_without_executing(session, admin_user):
    conv = await _conversation(session, admin_user)

    result = await AssistantToolService.invoke(
        session,
        conversation=conv,
        role_id=admin_user.role_id,
        tool_name="delete_everything",
        arguments={},
    )

    assert result.status == "failed"
    assert "unknown" in result.error.lower()
    # No tool invocation is recorded for a tool that does not exist.
    count = (
        await session.execute(
            select(func.count())
            .select_from(AssistantToolInvocation)
            .where(AssistantToolInvocation.conversation_id == conv.id)
        )
    ).scalar_one()
    assert count == 0


async def test_missing_required_argument_is_rejected(session, admin_user):
    conv = await _conversation(session, admin_user)

    result = await AssistantToolService.invoke(
        session,
        conversation=conv,
        role_id=admin_user.role_id,
        tool_name="recommend_pipeline",
        arguments={},  # experiment_id missing
    )

    assert result.status == "failed"
    assert "experiment_id" in result.error
    assert result.tool_invocation is not None
    assert result.tool_invocation.status == "failed"


async def test_launch_run_stops_at_plan_and_does_not_execute(session, admin_user):
    exp = await _bulk_mouse_experiment(session, admin_user)
    conv = await _conversation(session, admin_user)

    result = await AssistantToolService.invoke(
        session,
        conversation=conv,
        role_id=admin_user.role_id,
        tool_name="launch_run",
        arguments={
            "experiment_id": exp.id,
            "pipeline_key": "nf-core/rnaseq",
            "parameters": {"aligner": "star_salmon"},
            "reference_genome": "GRCm39",
        },
    )

    assert result.status == "awaiting_confirmation"
    assert result.tool_invocation.status == "awaiting_confirmation"
    assert result.tool_invocation.requires_confirmation is True
    assert result.action_plan is not None
    assert result.action_plan.status == "proposed"

    # The load-bearing assertion: nothing executed. No pipeline run was created.
    run_count = (await session.execute(select(func.count()).select_from(PipelineRun))).scalar_one()
    assert run_count == 0


# ---- Read-only discovery tools (list_experiments, list_samples, list_pipelines, check_status) ----


async def test_catalog_describes_read_only_discovery_tools():
    for name, permission in (
        ("list_experiments", ("experiments", "view")),
        ("list_samples", ("samples", "view")),
        ("list_pipelines", ("pipelines", "view")),
        ("check_status", ("pipelines", "view")),
    ):
        tool = get_tool(name)
        assert tool is not None, f"{name} not registered"
        assert tool.consequence_class == "read_only"
        assert tool.permission == permission


async def test_list_experiments_returns_org_experiments(session, admin_user):
    exp = await _bulk_mouse_experiment(session, admin_user)
    conv = await _conversation(session, admin_user)

    result = await AssistantToolService.invoke(
        session,
        conversation=conv,
        role_id=admin_user.role_id,
        tool_name="list_experiments",
        arguments={},
    )

    assert result.status == "succeeded"
    ids = [e["id"] for e in result.result["experiments"]]
    assert exp.id in ids
    assert result.tool_invocation.consequence_class == "read_only"
    assert await _audit_rows_for(session, result.tool_invocation.id) == 1


async def test_list_samples_returns_experiment_samples_with_assay(session, admin_user):
    exp = await _bulk_mouse_experiment(session, admin_user)
    conv = await _conversation(session, admin_user)

    result = await AssistantToolService.invoke(
        session,
        conversation=conv,
        role_id=admin_user.role_id,
        tool_name="list_samples",
        arguments={"experiment_id": exp.id},
    )

    assert result.status == "succeeded"
    samples = result.result["samples"]
    assert any(s["external_id"] == "B1" for s in samples)
    # The hybrid-assay fields the agent uses to reason are surfaced.
    assert all("assay" in s and "organism" in s for s in samples)


async def test_list_samples_rejects_experiment_outside_org(session, admin_user):
    conv = await _conversation(session, admin_user)

    result = await AssistantToolService.invoke(
        session,
        conversation=conv,
        role_id=admin_user.role_id,
        tool_name="list_samples",
        arguments={"experiment_id": 999999},  # not in this org
    )

    assert result.status == "failed"
    assert result.tool_invocation.status == "failed"


async def test_list_pipelines_returns_catalog(session, admin_user):
    await _bulk_mouse_experiment(session, admin_user)  # installs nf-core/rnaseq
    conv = await _conversation(session, admin_user)

    result = await AssistantToolService.invoke(
        session,
        conversation=conv,
        role_id=admin_user.role_id,
        tool_name="list_pipelines",
        arguments={},
    )

    assert result.status == "succeeded"
    keys = [p["pipeline_key"] for p in result.result["pipelines"]]
    assert "nf-core/rnaseq" in keys


async def test_check_status_returns_run_status(session, admin_user):
    exp = await _bulk_mouse_experiment(session, admin_user)
    run = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=exp.id,
        pipeline_name="nf-core/rnaseq",
        pipeline_version="3.14.0",
        status="running",
    )
    session.add(run)
    await session.flush()
    await session.commit()
    conv = await _conversation(session, admin_user)

    result = await AssistantToolService.invoke(
        session,
        conversation=conv,
        role_id=admin_user.role_id,
        tool_name="check_status",
        arguments={"run_id": run.id},
    )

    assert result.status == "succeeded"
    assert result.result["status"] == "running"
    assert result.result["id"] == run.id


async def test_check_status_rejects_run_outside_org(session, admin_user):
    conv = await _conversation(session, admin_user)

    result = await AssistantToolService.invoke(
        session,
        conversation=conv,
        role_id=admin_user.role_id,
        tool_name="check_status",
        arguments={"run_id": 999999},
    )

    assert result.status == "failed"


# ---- Results tools (get_metrics, explain_results): read-only, U3 results-in-chat ----


async def _completed_run(session, admin_user, *, status="succeeded"):
    """A pipeline run on a bulk-mouse experiment, in the given terminal status."""
    exp = await _bulk_mouse_experiment(session, admin_user)
    run = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=exp.id,
        pipeline_name="nf-core/rnaseq",
        pipeline_version="3.14.0",
        status=status,
        parameters_json={"aligner": "star_salmon"},
        output_files_json={"multiqc": "gs://results/multiqc.html"},
    )
    session.add(run)
    await session.flush()
    await session.commit()
    return exp, run


async def _add_qc_dashboard(session, admin_user, run, *, status="ready", metrics=None, summary="QC looks healthy."):
    dashboard = QCDashboard(
        organization_id=admin_user.organization_id,
        pipeline_run_id=run.id,
        experiment_id=run.experiment_id,
        metrics_json=metrics if metrics is not None else {"cell_count": 5000, "quality_rating": "pass"},
        summary_text=summary,
        status=status,
    )
    session.add(dashboard)
    await session.flush()
    await session.commit()
    return dashboard


async def test_catalog_describes_results_tools():
    for name in ("get_metrics", "explain_results"):
        tool = get_tool(name)
        assert tool is not None, f"{name} not registered"
        assert tool.consequence_class == "read_only"
        # Results reading is gated by experiments:view (a RESULTS_VIEW_PERMISSIONS member the
        # bench persona holds), so a non-computational user can ask about their own results.
        assert tool.permission == ("experiments", "view")


async def test_get_metrics_returns_qc_metrics_when_dashboard_ready(session, admin_user):
    _, run = await _completed_run(session, admin_user)
    await _add_qc_dashboard(
        session, admin_user, run, metrics={"cell_count": 4823, "median_genes_per_cell": 1200, "quality_rating": "pass"}
    )
    conv = await _conversation(session, admin_user)

    result = await AssistantToolService.invoke(
        session,
        conversation=conv,
        role_id=admin_user.role_id,
        tool_name="get_metrics",
        arguments={"run_id": run.id},
    )

    assert result.status == "succeeded"
    assert result.result["metrics_available"] is True
    assert result.result["run_id"] == run.id
    assert result.result["quality_rating"] == "pass"
    assert result.result["metrics"]["cell_count"] == 4823
    assert await _audit_rows_for(session, result.tool_invocation.id) == 1


async def test_get_metrics_reports_not_available_when_no_dashboard(session, admin_user):
    """A run with no QC dashboard yet does not error: the tool reports metrics_available=False with a
    reason so the agent can tell the user QC has not been generated, rather than crashing the turn."""
    _, run = await _completed_run(session, admin_user, status="running")
    conv = await _conversation(session, admin_user)

    result = await AssistantToolService.invoke(
        session,
        conversation=conv,
        role_id=admin_user.role_id,
        tool_name="get_metrics",
        arguments={"run_id": run.id},
    )

    assert result.status == "succeeded"
    assert result.result["metrics_available"] is False
    assert result.result["run_status"] == "running"
    assert result.result["reason"]


async def test_get_metrics_rejects_run_outside_org(session, admin_user):
    conv = await _conversation(session, admin_user)

    result = await AssistantToolService.invoke(
        session,
        conversation=conv,
        role_id=admin_user.role_id,
        tool_name="get_metrics",
        arguments={"run_id": 999999},
    )

    assert result.status == "failed"


async def test_explain_results_returns_interpretation_context(session, admin_user):
    """explain_results assembles the agent-review-style results context (run + params + samples + QC)
    so the assistant's own loop LLM can narrate it conversationally. The tool itself makes no LLM call."""
    _, run = await _completed_run(session, admin_user)
    await _add_qc_dashboard(session, admin_user, run, summary="High mapping rate; 4823 cells recovered.")
    conv = await _conversation(session, admin_user)

    result = await AssistantToolService.invoke(
        session,
        conversation=conv,
        role_id=admin_user.role_id,
        tool_name="explain_results",
        arguments={"run_id": run.id},
    )

    assert result.status == "succeeded"
    assert result.result["run_id"] == run.id
    assert result.result["run_status"] == "succeeded"
    assert result.result["quality_rating"] == "pass"
    md = result.result["results_markdown"]
    assert "Pipeline Run Review Input" in md
    assert "High mapping rate" in md  # the QC summary is folded into the interpretation context


async def test_explain_results_handles_run_without_dashboard(session, admin_user):
    """When QC has not been generated, explain_results still returns the run context (status, params,
    samples) so the assistant can explain where the run stands instead of failing."""
    _, run = await _completed_run(session, admin_user, status="failed")
    conv = await _conversation(session, admin_user)

    result = await AssistantToolService.invoke(
        session,
        conversation=conv,
        role_id=admin_user.role_id,
        tool_name="explain_results",
        arguments={"run_id": run.id},
    )

    assert result.status == "succeeded"
    assert result.result["run_status"] == "failed"
    assert result.result["quality_rating"] is None
    assert "Pipeline Run Review Input" in result.result["results_markdown"]


async def test_explain_results_rejects_run_outside_org(session, admin_user):
    conv = await _conversation(session, admin_user)

    result = await AssistantToolService.invoke(
        session,
        conversation=conv,
        role_id=admin_user.role_id,
        tool_name="explain_results",
        arguments={"run_id": 999999},
    )

    assert result.status == "failed"


async def test_launch_run_handler_folds_accessions_into_parameters():
    """Importing by accession is a fetchngs LAUNCH, not a separate import: the accessions ride in
    parameters so the built request stays a valid PipelineRunCreate (no top-level accessions field)."""
    from app.services.assistant_tool_catalog import _launch_run_handler

    out = await _launch_run_handler(
        None,
        org_id=1,
        user_id=1,
        arguments={
            "experiment_id": 5,
            "pipeline_key": "nf-core/fetchngs",
            "accessions": ["GSE123456", "SRR9999999"],
        },
    )
    assert out["experiment_id"] == 5
    assert out["pipeline_key"] == "nf-core/fetchngs"
    assert out["parameters"]["accessions"] == ["GSE123456", "SRR9999999"]


# ---- Mutating tools follow the same confirm gate as spend (owner rule) ----


async def test_install_catalog_descriptor():
    tool = get_tool("install")
    assert tool is not None
    assert tool.consequence_class == "mutating"
    # Mirrors the real POST /api/pipelines/registry/{name}/install guard.
    assert tool.permission == ("pipelines", "create")


async def test_install_is_mutating_and_stops_at_plan_without_executing(session, admin_user):
    """A mutating tool gets the SAME plan-then-confirm gate as spend: invoking it creates a plan and
    does NOT run the handler, so nothing is installed until the user confirms."""
    conv = await _conversation(session, admin_user)

    result = await AssistantToolService.invoke(
        session,
        conversation=conv,
        role_id=admin_user.role_id,
        tool_name="install",
        arguments={"name": "scrnaseq"},
    )

    assert result.status == "awaiting_confirmation"
    assert result.tool_invocation.consequence_class == "mutating"
    assert result.tool_invocation.requires_confirmation is True
    assert result.action_plan is not None
    # Nothing was installed at invoke time: no catalog entry for nf-core/scrnaseq exists.
    count = (
        await session.execute(
            select(func.count())
            .select_from(PipelineCatalogEntry)
            .where(PipelineCatalogEntry.pipeline_key == "nf-core/scrnaseq")
        )
    ).scalar_one()
    assert count == 0


async def test_launch_run_denied_when_caller_lacks_permission(session, admin_user, viewer_user):
    # viewer's role lacks pipelines:launch (the realistic persona is bench, which can use the
    # assistant but cannot launch; viewer exercises the same gate with an existing fixture).
    exp = await _bulk_mouse_experiment(session, admin_user)
    conv = await _conversation(session, viewer_user)

    result = await AssistantToolService.invoke(
        session,
        conversation=conv,
        role_id=viewer_user.role_id,
        tool_name="launch_run",
        arguments={"experiment_id": exp.id, "pipeline_key": "nf-core/rnaseq"},
    )

    assert result.status == "declined"
    assert "permission" in result.error.lower()
    assert result.tool_invocation.status == "declined"

    # Denied: no plan, nothing executed.
    plan_count = (
        await session.execute(
            select(func.count()).select_from(AssistantActionPlan).where(AssistantActionPlan.conversation_id == conv.id)
        )
    ).scalar_one()
    assert plan_count == 0
    run_count = (await session.execute(select(func.count()).select_from(PipelineRun))).scalar_one()
    assert run_count == 0
