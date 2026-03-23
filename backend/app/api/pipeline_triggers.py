from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.models.pipeline_run import PipelineRun
from app.schemas.pipeline_trigger import (
    CostEstimateResponse,
    PipelineTriggerCreate,
    PipelineTriggerResponse,
    PipelineTriggerUpdate,
)
from app.services.budget_service import BudgetService
from app.services.trigger_service import TriggerService

router = APIRouter(prefix="/api/pipeline-triggers", tags=["pipeline_triggers"])


def _trigger_response(t, stats: dict | None = None) -> PipelineTriggerResponse:
    return PipelineTriggerResponse(
        id=t.id,
        pipeline_id=t.pipeline_id,
        organization_id=t.organization_id,
        trigger_mode=t.trigger_mode,
        event_config=t.event_config,
        schedule_config=t.schedule_config,
        parameter_defaults=t.parameter_defaults,
        budget_config=t.budget_config,
        enabled=t.enabled,
        created_by=t.created_by,
        created_at=t.created_at,
        updated_at=t.updated_at,
        runs_triggered_7d=stats.get("runs_triggered_7d") if stats else None,
        runs_triggered_30d=stats.get("runs_triggered_30d") if stats else None,
    )


@router.get("", response_model=list[PipelineTriggerResponse])
async def list_triggers(
    current_user: dict = require_permission("pipelines", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    triggers = await TriggerService.list_triggers(session, org_id)
    results = []
    for t in triggers:
        stats = await TriggerService.get_trigger_stats(t.id, session)
        results.append(_trigger_response(t, stats))
    return results


@router.post("", response_model=PipelineTriggerResponse)
async def create_trigger(
    body: PipelineTriggerCreate,
    current_user: dict = require_permission("pipelines", "create"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    trigger = await TriggerService.create_trigger(session, org_id, user_id, body)
    await session.commit()
    trigger = await TriggerService.get_trigger(session, trigger.id)
    return _trigger_response(trigger)


@router.get("/queue")
async def list_queue(
    current_user: dict = require_permission("pipelines", "view"),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(PipelineRun).where(PipelineRun.status == "pending_budget_review").order_by(PipelineRun.created_at.asc())
    )
    runs = result.scalars().all()
    return [
        {
            "id": r.id,
            "pipeline_name": r.pipeline_name,
            "status": r.status,
            "cost_estimate": float(r.cost_estimate) if r.cost_estimate else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in runs
    ]


@router.get("/cost-estimates")
async def get_cost_estimates(
    current_user: dict = require_permission("pipelines", "view"),
    session: AsyncSession = Depends(get_session),
):
    """Per-pipeline estimation accuracy."""
    # Get distinct pipeline names from cost history
    from app.models.pipeline_cost_history import PipelineCostHistory
    from sqlalchemy import distinct

    result = await session.execute(select(distinct(PipelineCostHistory.pipeline_name)))
    names = [row[0] for row in result.fetchall()]
    estimates = []
    for name in names:
        accuracy = await BudgetService.get_estimation_accuracy(name, session)
        estimates.append(accuracy)
    return estimates


@router.get("/{trigger_id}", response_model=PipelineTriggerResponse)
async def get_trigger(
    trigger_id: int,
    current_user: dict = require_permission("pipelines", "view"),
    session: AsyncSession = Depends(get_session),
):
    trigger = await TriggerService.get_trigger(session, trigger_id)
    if not trigger:
        raise HTTPException(404, "Pipeline trigger not found")
    stats = await TriggerService.get_trigger_stats(trigger_id, session)
    return _trigger_response(trigger, stats)


@router.put("/{trigger_id}", response_model=PipelineTriggerResponse)
async def update_trigger(
    trigger_id: int,
    body: PipelineTriggerUpdate,
    current_user: dict = require_permission("pipelines", "edit"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    trigger = await TriggerService.update_trigger(session, trigger_id, user_id, body)
    if not trigger:
        raise HTTPException(404, "Pipeline trigger not found")
    await session.commit()
    trigger = await TriggerService.get_trigger(session, trigger_id)
    return _trigger_response(trigger)


@router.delete("/{trigger_id}", response_model=PipelineTriggerResponse)
async def disable_trigger(
    trigger_id: int,
    current_user: dict = require_permission("pipelines", "delete"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    trigger = await TriggerService.disable_trigger(session, trigger_id, user_id)
    if not trigger:
        raise HTTPException(404, "Pipeline trigger not found")
    await session.commit()
    trigger = await TriggerService.get_trigger(session, trigger_id)
    return _trigger_response(trigger)


@router.post("/{trigger_id}/test")
async def test_trigger(
    trigger_id: int,
    current_user: dict = require_permission("pipelines", "create"),
    session: AsyncSession = Depends(get_session),
):
    """Dry-run trigger against recent ingest events."""
    trigger = await TriggerService.get_trigger(session, trigger_id)
    if not trigger:
        raise HTTPException(404, "Pipeline trigger not found")

    # Get recent ingest events
    from app.models.ingest_event import IngestEvent

    result = await session.execute(
        select(IngestEvent)
        .where(IngestEvent.ingest_status == "cataloged")
        .order_by(IngestEvent.created_at.desc())
        .limit(10)
    )
    events = list(result.scalars().all())
    matches = []
    for ev in events:
        matched = TriggerService._match_trigger(trigger, ev)
        matches.append({"ingest_event_id": ev.id, "file_id": ev.file_id, "matched": matched})

    return {"trigger_id": trigger_id, "test_results": matches}


@router.post("/queue/{run_id}/approve")
async def approve_queued_run(
    run_id: int,
    current_user: dict = require_permission("pipelines", "create"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    run = await TriggerService.approve_queued_run(run_id, user_id, session)
    if not run:
        raise HTTPException(404, "Pipeline run not found")
    await session.commit()
    return {"id": run.id, "status": run.status}


@router.post("/queue/approve-bulk")
async def approve_bulk(
    body: dict,
    current_user: dict = require_permission("pipelines", "create"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    run_ids = body.get("run_ids", [])
    approved = await TriggerService.approve_queued_runs_bulk(run_ids, user_id, session)
    await session.commit()
    return {"approved_count": len(approved), "run_ids": [r.id for r in approved]}


# Budget endpoints
budget_router = APIRouter(tags=["budget"])


@budget_router.post("/api/pipeline-runs/estimate-cost", response_model=CostEstimateResponse)
async def estimate_cost(
    body: dict,
    current_user: dict = require_permission("pipelines", "create"),
    session: AsyncSession = Depends(get_session),
):
    pipeline_name = body.get("pipeline_name", "unknown")
    input_file_count = body.get("input_file_count", 1)
    input_total_bytes = body.get("input_total_bytes", 0)

    estimated_cost, ci_pct, history_count = await BudgetService.estimate_pipeline_cost(
        pipeline_name, input_file_count, input_total_bytes, session
    )
    budget_check = await BudgetService.check_budget(estimated_cost, ci_pct, session)

    return CostEstimateResponse(
        pipeline_name=pipeline_name,
        estimated_cost=estimated_cost,
        confidence_interval_pct=ci_pct,
        based_on_history_count=history_count,
        budget_check=budget_check,
    )


@budget_router.get("/api/budget/status")
async def budget_status(
    current_user: dict = require_permission("pipelines", "change_status"),
    session: AsyncSession = Depends(get_session),
):
    current_spend = await BudgetService.get_current_spend(session)
    monthly_budget = await BudgetService.get_monthly_budget(session)
    queued_cost = await BudgetService.get_queued_running_cost(session)
    return {
        "current_month_spend": current_spend,
        "monthly_budget": monthly_budget,
        "queued_running_cost": queued_cost,
        "remaining": monthly_budget - current_spend - queued_cost,
    }
