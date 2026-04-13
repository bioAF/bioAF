from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.schemas.backup import (
    BackupSettingsResponse,
    BackupSettingsUpdate,
    BackupStatusResponse,
    BackupTierStatus,
    ConfigSnapshot,
    ConfigSnapshotDiff,
    ConfigSnapshotListResponse,
    PostgresSnapshot,
    PostgresSnapshotListResponse,
    RestoreRequest,
    RestoreResponse,
    RestoreStatusResponse,
    StartPostgresRestoreRequest,
)
from app.services.backup_service import BackupService, RestoreService

router = APIRouter(prefix="/api/backups", tags=["backups"])


@router.get("/status", response_model=BackupStatusResponse)
async def get_backup_status(
    current_user: dict = require_permission("backups", "view"),
    session: AsyncSession = Depends(get_session),
):
    status = await BackupService.get_backup_status(session, current_user["org_id"])
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
        session,
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


@router.get("/postgres-snapshots", response_model=PostgresSnapshotListResponse)
async def list_postgres_snapshots(
    current_user: dict = require_permission("backups", "view"),
    session: AsyncSession = Depends(get_session),
):
    snapshots, total = await BackupService.get_postgres_snapshots(session, current_user["org_id"])
    return PostgresSnapshotListResponse(
        snapshots=[PostgresSnapshot(**s) for s in snapshots],
        total=total,
    )


@router.post("/trigger/postgres")
async def trigger_postgres_backup(
    current_user: dict = require_permission("backups", "create"),
    session: AsyncSession = Depends(get_session),
):
    """Trigger a manual PostgreSQL backup to GCS."""
    result = await BackupService.run_postgres_backup(session, current_user["org_id"])
    if result["status"] == "error":
        raise HTTPException(500, detail=result.get("message", "Backup failed"))
    return result


@router.post("/trigger/config")
async def trigger_config_backup(
    current_user: dict = require_permission("backups", "create"),
    session: AsyncSession = Depends(get_session),
):
    """Trigger a manual platform config backup to GCS."""
    result = await BackupService.run_config_backup(session, current_user["org_id"])
    if result["status"] == "error":
        raise HTTPException(500, detail=result.get("message", "Backup failed"))
    return result


@router.get("/tfstate-files")
async def list_tfstate_files(
    current_user: dict = require_permission("backups", "view"),
    session: AsyncSession = Depends(get_session),
):
    """List terraform state files available for download."""
    files = await BackupService.list_tfstate_files(session)
    return {"files": files}


@router.get("/tfstate-download/{filename:path}")
async def download_tfstate(
    filename: str,
    current_user: dict = require_permission("backups", "view"),
    session: AsyncSession = Depends(get_session),
):
    """Download a terraform state file."""
    data = await BackupService.download_tfstate(session, filename)
    if data is None:
        raise HTTPException(404, detail="Terraform state file not found")
    return Response(
        content=data,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename.split("/")[-1]}"'},
    )


@router.get("/settings", response_model=BackupSettingsResponse)
async def get_backup_settings(
    current_user: dict = require_permission("backups", "view"),
    session: AsyncSession = Depends(get_session),
):
    return await BackupService.get_backup_settings(session)


@router.put("/settings")
async def update_backup_settings(
    body: BackupSettingsUpdate,
    current_user: dict = require_permission("backups", "create"),
    session: AsyncSession = Depends(get_session),
):
    errors = []
    if body.postgres_retention_days is not None and body.postgres_retention_days < 1:
        errors.append("PostgreSQL backup retention must be at least 1 day")
    if body.postgres_schedule_hours is not None and body.postgres_schedule_hours < 1:
        errors.append("PostgreSQL backup schedule must be at least 1 hour")
    if body.config_retention_days is not None and body.config_retention_days < 1:
        errors.append("Config backup retention must be at least 1 day")
    if body.config_schedule_hours is not None and body.config_schedule_hours < 1:
        errors.append("Config backup schedule must be at least 1 hour")
    if errors:
        raise HTTPException(400, detail="; ".join(errors))

    updated = await BackupService.update_backup_settings(session, body.model_dump(exclude_unset=True))
    await session.commit()
    return {"status": "updated", "settings": updated}


# --- Database Restore ---


@router.get("/restore/status", response_model=RestoreStatusResponse)
async def get_restore_status(
    current_user: dict = require_permission("backups", "view"),
):
    return RestoreService.get_status()


@router.post("/restore/postgres")
async def start_postgres_restore(
    body: StartPostgresRestoreRequest,
    current_user: dict = require_permission("backups", "restore"),
    session: AsyncSession = Depends(get_session),
):
    """Start a database restore from a pg_dump backup. Enters review mode."""
    result = await RestoreService.start(session, current_user["org_id"], body.filename)
    if result["status"] == "error":
        status_code = 409 if "already active" in result["message"] else 500
        raise HTTPException(status_code, detail=result["message"])
    return result


@router.post("/restore/accept")
async def accept_restore(
    current_user: dict = require_permission("backups", "restore"),
):
    """Accept the restored database, making it permanent."""
    result = await RestoreService.accept()
    if result["status"] == "error":
        raise HTTPException(400, detail=result["message"])
    return result


@router.post("/restore/reject")
async def reject_restore(
    current_user: dict = require_permission("backups", "restore"),
):
    """Reject the restored database and revert to the original."""
    result = await RestoreService.reject()
    if result["status"] == "error":
        raise HTTPException(400, detail=result["message"])
    return result
