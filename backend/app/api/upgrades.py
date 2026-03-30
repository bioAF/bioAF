import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.api.dependencies import require_permission
from app.schemas.upgrade import (
    VersionInfo,
    UpdateCheckResponse,
    UpgradeHistoryItem,
    UpgradeHistoryListResponse,
    StartUpgradeRequest,
    StartUpgradeResponse,
    ConfirmUpgradeResponse,
    RollbackResponse,
)
from app.services.upgrade_service import UpgradeService

logger = logging.getLogger("bioaf.upgrades.api")

router = APIRouter(prefix="/api/upgrades", tags=["upgrades"])


@router.get("/current", response_model=VersionInfo)
async def get_current_version(
    current_user: dict = require_permission("experiments", "view"),
):
    return await UpgradeService.get_version_info()


@router.get("/check", response_model=UpdateCheckResponse)
async def check_for_updates(
    current_user: dict = require_permission("infrastructure", "view"),
):
    org_id = current_user["org_id"]
    return await UpgradeService.check_for_updates(org_id)


@router.get("/history", response_model=UpgradeHistoryListResponse)
async def get_upgrade_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: dict = require_permission("infrastructure", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = current_user["org_id"]
    upgrades, total = await UpgradeService.get_upgrade_history(session, org_id, page, page_size)
    return UpgradeHistoryListResponse(
        upgrades=[UpgradeHistoryItem.model_validate(u) for u in upgrades],
        total=total,
    )


@router.post("/start", response_model=StartUpgradeResponse)
async def start_upgrade(
    body: StartUpgradeRequest,
    current_user: dict = require_permission("infrastructure", "deploy"),
    session: AsyncSession = Depends(get_session),
):
    org_id = current_user["org_id"]
    user_id = int(current_user["sub"])
    upgrade = await UpgradeService.start_upgrade(session, org_id, body.target_version, user_id)
    await session.commit()
    return StartUpgradeResponse(
        upgrade_id=upgrade.id,
        status=upgrade.status,
        from_version=upgrade.from_version,
        to_version=upgrade.to_version,
        terraform_plan=upgrade.terraform_plan_json,
    )


@router.post("/{upgrade_id}/confirm", response_model=ConfirmUpgradeResponse)
async def confirm_upgrade(
    upgrade_id: int,
    current_user: dict = require_permission("infrastructure", "create"),
    session: AsyncSession = Depends(get_session),
):
    org_id = current_user["org_id"]
    try:
        upgrade = await UpgradeService.confirm_upgrade(session, org_id, upgrade_id)
        await session.commit()
        return ConfirmUpgradeResponse(
            upgrade_id=upgrade.id,
            status=upgrade.status,
            message=f"Upgrade to {upgrade.to_version} completed",
        )
    except ValueError as e:
        logger.warning("Upgrade confirm failed for %d: %s", upgrade_id, e)
        raise HTTPException(400, detail="Failed to confirm upgrade")


@router.post("/{upgrade_id}/rollback", response_model=RollbackResponse)
async def rollback_upgrade(
    upgrade_id: int,
    current_user: dict = require_permission("infrastructure", "build"),
    session: AsyncSession = Depends(get_session),
):
    org_id = current_user["org_id"]
    user_id = int(current_user["sub"])
    try:
        upgrade = await UpgradeService.rollback(session, org_id, upgrade_id, user_id)
        await session.commit()
        return RollbackResponse(
            upgrade_id=upgrade.id,
            status=upgrade.status,
            message=f"Upgrade to {upgrade.to_version} rolled back",
        )
    except ValueError as e:
        logger.warning("Upgrade rollback failed for %d: %s", upgrade_id, e)
        raise HTTPException(400, detail="Failed to rollback upgrade")
