import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory, get_session
from app.models.component import TerraformRun
from app.schemas.component import TerraformRunListResponse, TerraformRunResponse
from app.services.terraform_executor import TerraformExecutor
from app.services.terraform_service import TerraformService

logger = logging.getLogger("bioaf.terraform")

router = APIRouter(prefix="/api/terraform", tags=["terraform"])


def _require_admin(request: Request) -> dict:
    current_user = request.state.current_user
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


@router.get("/runs", response_model=TerraformRunListResponse)
async def list_runs(request: Request, session: AsyncSession = Depends(get_session)):
    _require_admin(request)
    runs = await TerraformService.list_runs(session)
    return TerraformRunListResponse(
        runs=[
            TerraformRunResponse(
                id=r.id,
                triggered_by_user_id=r.triggered_by_user_id,
                action=r.action,
                component_key=r.component_key,
                plan_summary=r.plan_summary_json,
                status=r.status,
                started_at=r.started_at,
                completed_at=r.completed_at,
                error_message=r.error_message,
            )
            for r in runs
        ],
        total=len(runs),
    )


@router.get("/runs/active", response_model=TerraformRunResponse | None)
async def get_active_run(request: Request, session: AsyncSession = Depends(get_session)):
    _require_admin(request)
    run = await TerraformService.get_active_run(session)
    if not run:
        return None
    return TerraformRunResponse(
        id=run.id,
        triggered_by_user_id=run.triggered_by_user_id,
        action=run.action,
        component_key=run.component_key,
        plan_summary=run.plan_summary_json,
        status=run.status,
        started_at=run.started_at,
        completed_at=run.completed_at,
        error_message=run.error_message,
    )


@router.get("/runs/{run_id}", response_model=TerraformRunResponse)
async def get_run(run_id: int, request: Request, session: AsyncSession = Depends(get_session)):
    _require_admin(request)
    run = await TerraformService.get_run(session, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return TerraformRunResponse(
        id=run.id,
        triggered_by_user_id=run.triggered_by_user_id,
        action=run.action,
        component_key=run.component_key,
        plan_summary=run.plan_summary_json,
        status=run.status,
        started_at=run.started_at,
        completed_at=run.completed_at,
        error_message=run.error_message,
    )


async def _run_apply_background(run_id: int, user_id: int) -> None:
    """Run TerraformExecutor.run_apply in the background with its own session."""
    async with async_session_factory() as bg_session:
        try:
            async for _event in TerraformExecutor.run_apply(bg_session, run_id, user_id):
                pass
            await bg_session.commit()
        except Exception:
            logger.exception("Background terraform apply failed for run %d", run_id)
            await bg_session.rollback()


@router.post("/runs/{run_id}/confirm", response_model=TerraformRunResponse)
async def confirm_run(run_id: int, request: Request, session: AsyncSession = Depends(get_session)):
    current_user = _require_admin(request)
    user_id = int(current_user["sub"])

    # Check if this is a module-based run (created by TerraformExecutor)
    result = await session.execute(select(TerraformRun).where(TerraformRun.id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status != "awaiting_confirmation":
        raise HTTPException(status_code=400, detail=f"Run is not awaiting confirmation (status: {run.status})")

    if run.module_name:
        # Module-based run: kick off apply in background, return immediately
        run.status = "applying"
        await session.commit()
        asyncio.create_task(_run_apply_background(run_id, user_id))
    else:
        # Legacy run: use TerraformService (synchronous)
        try:
            run = await TerraformService.apply_plan(session, run_id, user_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        await session.commit()

    return TerraformRunResponse(
        id=run.id,
        triggered_by_user_id=run.triggered_by_user_id,
        action=run.action,
        component_key=run.component_key,
        plan_summary=run.plan_summary_json,
        status=run.status,
        started_at=run.started_at,
        completed_at=run.completed_at,
        error_message=run.error_message,
    )


@router.post("/runs/{run_id}/cancel", response_model=TerraformRunResponse)
async def cancel_run(run_id: int, request: Request, session: AsyncSession = Depends(get_session)):
    current_user = _require_admin(request)
    user_id = int(current_user["sub"])

    try:
        run = await TerraformService.cancel_run(session, run_id, user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await session.commit()
    return TerraformRunResponse(
        id=run.id,
        triggered_by_user_id=run.triggered_by_user_id,
        action=run.action,
        component_key=run.component_key,
        plan_summary=run.plan_summary_json,
        status=run.status,
        started_at=run.started_at,
        completed_at=run.completed_at,
        error_message=run.error_message,
    )
