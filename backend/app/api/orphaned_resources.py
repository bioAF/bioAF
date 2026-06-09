"""API endpoints for orphaned resource tracking and cleanup."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.schemas.orphaned_resource import (
    OrphanedResourceListResponse,
    OrphanedResourceResponse,
)
from app.services.orphaned_resource_service import OrphanedResourceService

router = APIRouter(tags=["orphaned_resources"])


class RecoveryCheckItem(BaseModel):
    id: int
    resource_name: str
    gcp_project_id: str
    gcp_zone: str | None
    stack_uid: str
    gke_status: str
    detected_at: str | None


class RecoveryCheckResponse(BaseModel):
    recoverable: list[RecoveryCheckItem]
    provisioning: list[RecoveryCheckItem]
    dead: list[RecoveryCheckItem]


class CleanupAllResponse(BaseModel):
    cleaned: int
    skipped: int
    failed: int


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


# Static path endpoints MUST be registered before {resource_id} to avoid
# FastAPI matching "recovery-check" or "cleanup-all" as a path parameter.


@router.get(
    "/api/v1/infrastructure/orphaned-resources/recovery-check",
    response_model=RecoveryCheckResponse,
)
async def recovery_check(
    current_user: dict = require_permission("infrastructure", "deploy"),
    session: AsyncSession = Depends(get_session),
) -> RecoveryCheckResponse:
    """Check all orphaned GKE clusters and classify as recoverable, provisioning, or dead."""
    result = await OrphanedResourceService.recovery_check(session)
    return RecoveryCheckResponse(
        recoverable=[RecoveryCheckItem(**item) for item in result["recoverable"]],
        provisioning=[RecoveryCheckItem(**item) for item in result["provisioning"]],
        dead=[RecoveryCheckItem(**item) for item in result["dead"]],
    )


@router.post(
    "/api/v1/infrastructure/orphaned-resources/cleanup-all",
    response_model=CleanupAllResponse,
)
async def cleanup_all_orphaned_resources(
    current_user: dict = require_permission("infrastructure", "deploy"),
    session: AsyncSession = Depends(get_session),
) -> CleanupAllResponse:
    """Clean up all dead orphaned GKE clusters in one shot.

    Skips clusters that are RUNNING (should be adopted) or PROVISIONING
    (still starting). Deletes ERROR clusters and dismisses NOT_FOUND ones.
    """
    user_id = int(current_user["sub"])
    result = await OrphanedResourceService.cleanup_dead_orphans(session, user_id)
    await session.commit()
    return CleanupAllResponse(**result)


# Dynamic path endpoints with {resource_id} come after static paths.


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
    resource = await OrphanedResourceService.cleanup_resource(session, resource_id, user_id)
    await session.commit()
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
    resource = await OrphanedResourceService.dismiss_resource(session, resource_id, user_id)
    await session.commit()
    return OrphanedResourceResponse.model_validate(resource)


@router.post(
    "/api/v1/infrastructure/orphaned-resources/{resource_id}/adopt",
    response_model=OrphanedResourceResponse,
)
async def adopt_orphaned_resource(
    resource_id: int,
    current_user: dict = require_permission("infrastructure", "deploy"),
    session: AsyncSession = Depends(get_session),
) -> OrphanedResourceResponse:
    """Adopt an orphaned GKE cluster that is actually running.

    Populates platform_config with cluster details and marks the resource
    as adopted. Returns 409 if the cluster is not in a running state.
    """
    user_id = int(current_user["sub"])
    resource = await OrphanedResourceService.adopt_resource(session, resource_id, user_id)
    await session.commit()
    return OrphanedResourceResponse.model_validate(resource)
