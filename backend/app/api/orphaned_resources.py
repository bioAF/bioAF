"""API endpoints for orphaned resource tracking and cleanup."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.schemas.orphaned_resource import (
    OrphanedResourceListResponse,
    OrphanedResourceResponse,
)
from app.services.orphaned_resource_service import OrphanedResourceService

logger = logging.getLogger("bioaf.orphaned_resources.api")

router = APIRouter(tags=["orphaned_resources"])


@router.get(
    "/api/v1/infrastructure/orphaned-resources",
    response_model=OrphanedResourceListResponse,
)
async def list_orphaned_resources(
    status: str | None = None,
    current_user: dict = require_permission("infrastructure", "view"),
    session: AsyncSession = Depends(get_session),
) -> OrphanedResourceListResponse:
    """List orphaned GCP resources, optionally filtered by status."""
    resources = await OrphanedResourceService.list_resources(session, status=status)
    return OrphanedResourceListResponse(
        items=[OrphanedResourceResponse.model_validate(r) for r in resources],
        total=len(resources),
    )


@router.post(
    "/api/v1/infrastructure/orphaned-resources/{resource_id}/cleanup",
    response_model=OrphanedResourceResponse,
)
async def cleanup_orphaned_resource(
    resource_id: int,
    current_user: dict = require_permission("infrastructure", "view"),
    session: AsyncSession = Depends(get_session),
) -> OrphanedResourceResponse:
    """Trigger cleanup of an orphaned resource (deletes from GCP)."""
    user_id = int(current_user["sub"])
    try:
        resource = await OrphanedResourceService.cleanup_resource(session, resource_id, user_id)
        await session.commit()
    except ValueError as exc:
        logger.warning("Orphaned resource cleanup failed for %d: %s", resource_id, exc)
        raise HTTPException(status_code=404, detail="Resource not found")
    return OrphanedResourceResponse.model_validate(resource)


@router.post(
    "/api/v1/infrastructure/orphaned-resources/{resource_id}/dismiss",
    response_model=OrphanedResourceResponse,
)
async def dismiss_orphaned_resource(
    resource_id: int,
    current_user: dict = require_permission("infrastructure", "view"),
    session: AsyncSession = Depends(get_session),
) -> OrphanedResourceResponse:
    """Mark an orphaned resource as manually resolved."""
    user_id = int(current_user["sub"])
    try:
        resource = await OrphanedResourceService.dismiss_resource(session, resource_id, user_id)
        await session.commit()
    except ValueError as exc:
        logger.warning("Orphaned resource dismiss failed for %d: %s", resource_id, exc)
        raise HTTPException(status_code=404, detail="Resource not found")
    return OrphanedResourceResponse.model_validate(resource)
