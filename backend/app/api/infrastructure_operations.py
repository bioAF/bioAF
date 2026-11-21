"""Infrastructure operation status, outcomes and recovery.

- ``GET  /api/v1/infrastructure/operations/active``: whether an infrastructure operation holds
  exclusion. Every signed-in user gets the busy signal (mockup M4); administrators (infrastructure
  deploy or configure) also get the operation and unseen interrupted outcomes (M3, M9).
- ``GET  /api/v1/infrastructure/operations``: recent operations (administrators).
- ``GET  /api/v1/infrastructure/operations/{id}``: one operation, for the status popup (M2, M5).
- ``POST /api/v1/infrastructure/operations/{id}/seen``: an administrator opened its outcome (M9).
- ``POST /api/v1/infrastructure/operations/{id}/recover``: audited recovery (M10, configure).

Status reads never start, stop or change infrastructure work.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import has_effective_permission, require_any_permission, require_permission, signed_in
from app.database import get_session
from app.exceptions import NotFoundError
from app.models.infrastructure_operation import InfrastructureOperation
from app.services import infrastructure_operations as ops

router = APIRouter(tags=["infrastructure_operations"])

ADMIN_PERMISSIONS = [("infrastructure", "deploy"), ("infrastructure", "configure")]

OperationStatus = Literal[
    "accepted", "running", "stopping", "succeeded", "failed", "cancelled", "interrupted", "recovery_required"
]


class OperationStep(BaseModel):
    key: str
    label: str
    state: str
    detail: str | None = None
    completed: int | None = None
    total: int | None = None


class OperationView(BaseModel):
    id: int
    action: str
    label: str
    status: OperationStatus
    phase: str | None = None
    started_by: str | None = None
    accepted_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    steps: list[OperationStep] = Field(default_factory=list)
    message: str | None = None
    error_category: str | None = None
    partial_effects: dict | None = None
    cancel_requested_at: datetime | None = None
    cancel_requested_by: str | None = None
    cancel_deadline: datetime | None = None
    recovered_at: datetime | None = None
    outcome_seen_at: datetime | None = None


class ActiveOperationResponse(BaseModel):
    # An operation holds installation-wide exclusion: infrastructure actions are unavailable.
    busy: bool
    # "running" while work is accepted, running or stopping; "recovery_required" when paused.
    state: Literal["running", "recovery_required"] | None = None
    # Administrators only: the operation holding exclusion.
    operation: OperationView | None = None
    # Administrators only: interrupted outcomes nobody has opened yet (one-time banner).
    interrupted: list[OperationView] = Field(default_factory=list)


class OperationListResponse(BaseModel):
    operations: list[OperationView]


class RecoverRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)
    confirm: bool = False


async def _view(session: AsyncSession, op: InfrastructureOperation) -> OperationView:
    data = await ops.describe(session, op)
    data.pop("started_by_user_id", None)
    data.pop("child_run_ids", None)
    return OperationView(**data)


async def _is_admin(session: AsyncSession, user: dict) -> bool:
    for resource, action in ADMIN_PERMISSIONS:
        if await has_effective_permission(session, user, resource, action):
            return True
    return False


@router.get("/api/v1/infrastructure/operations/active", response_model=ActiveOperationResponse)
async def active_operation_endpoint(
    current_user: dict = signed_in(),
    session: AsyncSession = Depends(get_session),
) -> ActiveOperationResponse:
    active = await ops.active_operation(session)
    state = None
    if active is not None:
        state = "recovery_required" if active.status == "recovery_required" else "running"
    response = ActiveOperationResponse(busy=active is not None, state=state)
    if not await _is_admin(session, current_user):
        return response
    if active is not None:
        response.operation = await _view(session, active)
    unseen = (
        await session.execute(
            select(InfrastructureOperation)
            .where(
                InfrastructureOperation.status == "interrupted",
                InfrastructureOperation.outcome_seen_at.is_(None),
                InfrastructureOperation.error_category.in_(("interrupted", "not_started")),
            )
            .order_by(InfrastructureOperation.id.desc())
            .limit(5)
        )
    ).scalars()
    response.interrupted = [await _view(session, op) for op in unseen]
    return response


@router.get("/api/v1/infrastructure/operations", response_model=OperationListResponse)
async def list_operations_endpoint(
    limit: int = 20,
    current_user: dict = require_any_permission(ADMIN_PERMISSIONS),
    session: AsyncSession = Depends(get_session),
) -> OperationListResponse:
    limit = max(1, min(limit, 100))
    rows = (
        await session.execute(select(InfrastructureOperation).order_by(InfrastructureOperation.id.desc()).limit(limit))
    ).scalars()
    return OperationListResponse(operations=[await _view(session, op) for op in rows])


async def _get(session: AsyncSession, operation_id: int) -> InfrastructureOperation:
    op = await ops.get_operation(session, operation_id)
    if op is None:
        raise NotFoundError("Operation not found")
    return op


@router.get("/api/v1/infrastructure/operations/{operation_id}", response_model=OperationView)
async def get_operation_endpoint(
    operation_id: int,
    current_user: dict = require_any_permission(ADMIN_PERMISSIONS),
    session: AsyncSession = Depends(get_session),
) -> OperationView:
    return await _view(session, await _get(session, operation_id))


@router.post("/api/v1/infrastructure/operations/{operation_id}/seen", response_model=OperationView)
async def mark_operation_seen_endpoint(
    operation_id: int,
    current_user: dict = require_any_permission(ADMIN_PERMISSIONS),
    session: AsyncSession = Depends(get_session),
) -> OperationView:
    await _get(session, operation_id)
    await ops.mark_seen(session, operation_id)
    await session.commit()
    return await _view(session, await _get(session, operation_id))


@router.post("/api/v1/infrastructure/operations/{operation_id}/recover", response_model=OperationView)
async def recover_operation_endpoint(
    operation_id: int,
    body: RecoverRequest,
    current_user: dict = require_permission("infrastructure", "configure"),
    session: AsyncSession = Depends(get_session),
) -> OperationView:
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Confirm that this Terraform process is no longer running.")
    op = await ops.recover(session, operation_id, int(current_user["sub"]), reason=body.reason)
    await session.commit()
    return await _view(session, op)
