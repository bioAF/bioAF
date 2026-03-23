from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.api.dependencies import require_permission
from app.schemas.cost import (
    CostSummaryResponse,
    CostHistoryResponse,
    DailyCost,
    BudgetConfigResponse,
    BudgetConfigUpdate,
)
from app.models.cost_record import CostRecord
from app.services.cost_service import CostService

router = APIRouter(prefix="/api/costs", tags=["costs"])


@router.get("/summary", response_model=CostSummaryResponse)
async def get_cost_summary(
    current_user: dict = require_permission("cost_center", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = current_user["org_id"]
    data = await CostService.get_cost_summary(session, org_id)
    return CostSummaryResponse(**data)


@router.get("/history", response_model=CostHistoryResponse)
async def get_cost_history(
    start_date: date = Query(...),
    end_date: date = Query(...),
    current_user: dict = require_permission("cost_center", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = current_user["org_id"]
    records, total = await CostService.get_cost_history(session, org_id, start_date, end_date)
    return CostHistoryResponse(
        records=[DailyCost(**r) for r in records],
        total_amount=total,
    )


@router.get("/budget", response_model=BudgetConfigResponse)
async def get_budget_config(
    current_user: dict = require_permission("cost_center", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = current_user["org_id"]
    config = await CostService.get_budget_config(session, org_id)
    if config:
        return BudgetConfigResponse.model_validate(config)
    return BudgetConfigResponse(
        threshold_50_enabled=True,
        threshold_80_enabled=True,
        threshold_100_enabled=True,
        scale_to_zero_on_100=False,
    )


@router.put("/budget", response_model=BudgetConfigResponse)
async def update_budget_config(
    body: BudgetConfigUpdate,
    current_user: dict = require_permission("cost_center", "configure_budgets"),
    session: AsyncSession = Depends(get_session),
):
    org_id = current_user["org_id"]
    config = await CostService.update_budget_config(session, org_id, body.model_dump(exclude_unset=True))
    await session.commit()
    return BudgetConfigResponse.model_validate(config)


@router.post("/sync")
async def trigger_billing_sync(
    current_user: dict = require_permission("cost_center", "configure_budgets"),
    session: AsyncSession = Depends(get_session),
):
    org_id = current_user["org_id"]
    await CostService.sync_billing_data(session, org_id)
    await session.commit()
    return {"status": "sync_initiated"}


@router.delete("/records")
async def purge_cost_records(
    start_date: date = Query(...),
    end_date: date = Query(...),
    current_user: dict = require_permission("cost_center", "configure_budgets"),
    session: AsyncSession = Depends(get_session),
):
    """Delete cost records in a date range. Useful for clearing stale data."""
    from sqlalchemy import delete

    org_id = current_user["org_id"]
    result = await session.execute(
        delete(CostRecord).where(
            CostRecord.organization_id == org_id,
            CostRecord.record_date >= start_date,
            CostRecord.record_date <= end_date,
        )
    )
    await session.commit()
    return {"deleted_count": result.rowcount}
