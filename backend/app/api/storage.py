from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.services.storage_service import StorageService

router = APIRouter(prefix="/api/storage", tags=["storage"])


@router.get("/stats")
async def get_storage_stats(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])
    return await StorageService.get_storage_stats(session, org_id)


@router.post("/refresh")
async def refresh_storage_stats(
    current_user: dict = require_permission("infrastructure", "create"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    stats = await StorageService.refresh_storage_stats(session, org_id)
    await session.commit()
    return stats


@router.get("/lifecycle")
async def get_lifecycle_policies(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])
    policies = await StorageService.get_lifecycle_policies(session, org_id)
    return {"policies": policies}
