"""Phase 17 Terraform executor API endpoints.

Registers under /api/v1/infrastructure/terraform/ and provides:
- POST /bootstrap         - SSE stream for foundation bootstrap
- POST /plan              - synchronous plan, returns JSON result
- POST /apply/{run_id}    - SSE stream for apply
- GET  /status            - current terraform state
- GET  /runs/{run_id}     - run detail
- GET  /runs              - recent run history (last 10)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.models.component import TerraformRun
from app.services.terraform_executor import TerraformExecutor, TerraformProgressEvent

logger = logging.getLogger("bioaf.terraform_api")

router = APIRouter(prefix="/api/v1/infrastructure/terraform", tags=["terraform_executor"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class TerraformPlanRequest(BaseModel):
    module_name: str = "foundation"


class TerraformRunDetail(BaseModel):
    id: int
    action: str
    module_name: str | None = None
    status: str
    resources_planned: int | None = None
    resources_completed: int = 0
    plan_json: dict | None = None
    triggered_by_user_id: int
    started_at: datetime
    completed_at: datetime | None = None
    error_message: str | None = None
    terraform_state_url: str | None = None

    model_config = {"from_attributes": True}


class TerraformRunListResponse(BaseModel):
    runs: list[TerraformRunDetail]
    total: int


class TerraformStatusResponse(BaseModel):
    terraform_initialized: bool
    terraform_state_bucket: str
    gcp_credentials_configured: bool
    active_run_id: int | None = None
    active_run_status: str | None = None


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------


def _sse_event(event: TerraformProgressEvent) -> str:
    """Format a TerraformProgressEvent as an SSE data line."""
    payload = {
        "event_type": event.event_type,
        "message": event.message,
        "resource_address": event.resource_address,
        "resources_completed": event.resources_completed,
        "resources_total": event.resources_total,
        "log_line": event.log_line,
    }
    return f"data: {json.dumps(payload)}\n\n"


async def _stream_events(
    gen: AsyncIterator[TerraformProgressEvent],
) -> AsyncIterator[str]:
    """Wrap an async event generator into SSE-formatted strings."""
    try:
        async for event in gen:
            yield _sse_event(event)
    except Exception as exc:
        error_event = TerraformProgressEvent(
            event_type="apply_error",
            message=str(exc),
        )
        yield _sse_event(error_event)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/bootstrap")
async def bootstrap_foundation(
    current_user: dict = require_permission("infrastructure", "create"),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Bootstrap the GCS Terraform state bucket. Streams SSE progress events."""
    user_id = int(current_user["sub"])
    org_id = int(current_user["org_id"])

    # Eagerly check preconditions before opening the stream so we can return 409
    config_rows = (
        await session.execute(
            text(
                "SELECT key, value FROM platform_config "
                "WHERE key IN ('gcp_credentials_configured', 'terraform_initialized')"
            )
        )
    ).fetchall()
    config = {r[0]: r[1] for r in config_rows}

    if config.get("gcp_credentials_configured", "false") != "true":
        raise HTTPException(status_code=409, detail="GCP credentials are not configured")
    if config.get("terraform_initialized", "false") == "true":
        raise HTTPException(status_code=409, detail="Infrastructure is already initialized")

    gen = TerraformExecutor.bootstrap_foundation(session=session, user_id=user_id, org_id=org_id)

    async def event_generator():
        try:
            async for event in gen:
                yield _sse_event(event)
        except Exception as exc:
            error_event = TerraformProgressEvent(
                event_type="apply_error",
                message=str(exc),
            )
            yield _sse_event(error_event)
        finally:
            await session.commit()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/plan", response_model=TerraformRunDetail)
async def run_plan(
    body: TerraformPlanRequest,
    current_user: dict = require_permission("infrastructure", "create"),
    session: AsyncSession = Depends(get_session),
) -> TerraformRunDetail:
    """Run terraform plan and return the plan summary."""
    user_id = int(current_user["sub"])

    try:
        run = await TerraformExecutor.run_plan(
            session=session,
            user_id=user_id,
            module_name=body.module_name,
        )
        await session.commit()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return TerraformRunDetail.model_validate(run)


@router.post("/apply/{run_id}")
async def apply_plan(
    run_id: int,
    current_user: dict = require_permission("infrastructure", "create"),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Apply an approved plan. Streams SSE progress events."""
    user_id = int(current_user["sub"])

    # Validate run exists and is awaiting confirmation
    result = await session.execute(select(TerraformRun).where(TerraformRun.id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status != "awaiting_confirmation":
        raise HTTPException(
            status_code=409,
            detail=f"Run {run_id} is not awaiting confirmation (status: {run.status})",
        )

    gen = TerraformExecutor.run_apply(session=session, run_id=run_id, user_id=user_id)

    async def event_generator():
        try:
            async for event in gen:
                yield _sse_event(event)
        except Exception as exc:
            error_event = TerraformProgressEvent(
                event_type="apply_error",
                message=str(exc),
            )
            yield _sse_event(error_event)
        finally:
            await session.commit()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/abandon/{run_id}", response_model=TerraformRunDetail)
async def abandon_run(
    run_id: int,
    current_user: dict = require_permission("infrastructure", "create"),
    session: AsyncSession = Depends(get_session),
) -> TerraformRunDetail:
    """Abandon a stuck Terraform run and release the GCS state lock."""
    user_id = int(current_user["sub"])

    try:
        run = await TerraformExecutor.abandon_run(
            session=session,
            run_id=run_id,
            user_id=user_id,
        )
        await session.commit()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return TerraformRunDetail.model_validate(run)


@router.get("/status", response_model=TerraformStatusResponse)
async def get_terraform_status(
    current_user: dict = require_permission("infrastructure", "change_status"),
    session: AsyncSession = Depends(get_session),
) -> TerraformStatusResponse:
    """Return current terraform initialization state and active run (if any)."""
    keys = [
        "terraform_initialized",
        "terraform_state_bucket",
        "gcp_credentials_configured",
    ]
    rows = (
        await session.execute(
            text("SELECT key, value FROM platform_config WHERE key = ANY(:keys)").bindparams(keys=keys)
        )
    ).fetchall()
    config = {r[0]: r[1] for r in rows}

    # Check for active run (includes awaiting_confirmation since those hold GCS locks)
    active_result = await session.execute(
        select(TerraformRun).where(TerraformRun.status.in_(["planning", "applying", "awaiting_confirmation"]))
    )
    active_run = active_result.scalar_one_or_none()

    return TerraformStatusResponse(
        terraform_initialized=config.get("terraform_initialized", "false") == "true",
        terraform_state_bucket=config.get("terraform_state_bucket", ""),
        gcp_credentials_configured=config.get("gcp_credentials_configured", "false") == "true",
        active_run_id=active_run.id if active_run else None,
        active_run_status=active_run.status if active_run else None,
    )


@router.get("/runs", response_model=TerraformRunListResponse)
async def list_runs(
    current_user: dict = require_permission("infrastructure", "view"),
    session: AsyncSession = Depends(get_session),
) -> TerraformRunListResponse:
    """Return the last 10 Terraform runs."""
    result = await session.execute(select(TerraformRun).order_by(TerraformRun.started_at.desc()).limit(10))
    runs = list(result.scalars().all())
    return TerraformRunListResponse(
        runs=[TerraformRunDetail.model_validate(r) for r in runs],
        total=len(runs),
    )


@router.get("/runs/{run_id}", response_model=TerraformRunDetail)
async def get_run(
    run_id: int,
    current_user: dict = require_permission("infrastructure", "view"),
    session: AsyncSession = Depends(get_session),
) -> TerraformRunDetail:
    """Return a single Terraform run by ID."""
    result = await session.execute(select(TerraformRun).where(TerraformRun.id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return TerraformRunDetail.model_validate(run)
