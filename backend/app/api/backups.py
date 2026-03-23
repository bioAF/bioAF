from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.api.dependencies import require_permission
from app.schemas.backup import (
    BackupStatusResponse,
    BackupTierStatus,
    ConfigSnapshotListResponse,
    ConfigSnapshot,
    ConfigSnapshotDiff,
    RestoreRequest,
    RestoreResponse,
    BackupSettingsUpdate,
)
from app.services.backup_service import BackupService

router = APIRouter(prefix="/api/backups", tags=["backups"])


@router.get("/status", response_model=BackupStatusResponse)
async def get_backup_status(
    current_user: dict = require_permission("backups", "view"),
    session: AsyncSession = Depends(get_session),
):
    status = await BackupService.get_backup_status(current_user["org_id"])
    return BackupStatusResponse(
        tiers=[BackupTierStatus(**t) for t in status["tiers"]],
        overall_status=status["overall_status"],
    )


@router.get("/config-snapshots", response_model=ConfigSnapshotListResponse)
async def list_config_snapshots(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: dict = require_permission("backups", "view"),
    session: AsyncSession = Depends(get_session),
):
    snapshots, total = await BackupService.get_config_snapshots(
        current_user["org_id"],
        page,
        page_size,
    )
    return ConfigSnapshotListResponse(
        snapshots=[ConfigSnapshot(**s) for s in snapshots],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/config-snapshots/{date}/diff", response_model=ConfigSnapshotDiff)
async def get_config_snapshot_diff(
    date: str,
    current_user: dict = require_permission("backups", "view"),
    session: AsyncSession = Depends(get_session),
):
    diff = await BackupService.get_config_snapshot_diff(current_user["org_id"], date)
    return ConfigSnapshotDiff(**diff)


@router.post("/restore/config", response_model=RestoreResponse)
async def restore_config(
    body: RestoreRequest,
    current_user: dict = require_permission("backups", "restore"),
    session: AsyncSession = Depends(get_session),
):
    result = await BackupService.restore_config(
        current_user["org_id"],
        body.restore_point or "latest",
    )
    return RestoreResponse(**result)


@router.post("/restore/cloudsql", response_model=RestoreResponse)
async def restore_cloudsql(
    body: RestoreRequest,
    current_user: dict = require_permission("backups", "restore"),
    session: AsyncSession = Depends(get_session),
):
    return RestoreResponse(
        status="initiated",
        message=f"Cloud SQL PITR restore to {body.restore_point or 'latest'} initiated",
    )


@router.post("/restore/filestore", response_model=RestoreResponse)
async def restore_filestore(
    body: RestoreRequest,
    current_user: dict = require_permission("backups", "restore"),
    session: AsyncSession = Depends(get_session),
):
    return RestoreResponse(
        status="initiated",
        message=f"Filestore snapshot restore to {body.restore_point or 'latest'} initiated",
    )


@router.put("/settings")
async def update_backup_settings(
    body: BackupSettingsUpdate,
    current_user: dict = require_permission("backups", "create"),
    session: AsyncSession = Depends(get_session),
):
    # Enforce minimums
    errors = []
    if body.cloud_sql_pitr_days is not None and body.cloud_sql_pitr_days < 7:
        errors.append("Cloud SQL PITR retention must be at least 7 days")
    if body.cloud_sql_retention_days is not None and body.cloud_sql_retention_days < 30:
        errors.append("Cloud SQL snapshot retention must be at least 30 days")
    if errors:
        from fastapi import HTTPException

        raise HTTPException(400, detail="; ".join(errors))

    return {"status": "updated", "settings": body.model_dump(exclude_unset=True)}
