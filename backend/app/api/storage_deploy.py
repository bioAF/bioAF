"""Phase 18 Storage infrastructure endpoints.

- POST /api/v1/infrastructure/storage/deploy  - deploy GCS buckets via Terraform
- GET  /api/v1/infrastructure/storage/buckets  - live bucket metrics (replaces hardcoded)
- POST /api/v1/files/{file_id}/assign          - assign file to experiment
- POST /api/v1/files/{file_id}/unlink          - unlink file from experiment
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.services.file_organization import FileOrganizationService
from app.services.gcs_storage import BucketMetrics, GcsStorageService

logger = logging.getLogger("bioaf.storage_deploy_api")

router = APIRouter(tags=["storage_deploy"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class FileAssignRequest(BaseModel):
    experiment_id: int


class BucketMetricsResponse(BaseModel):
    buckets: list[BucketMetrics]


class StorageDeployResponse(BaseModel):
    status: str
    message: str = ""


# ---------------------------------------------------------------------------
# Deploy endpoint
# ---------------------------------------------------------------------------


async def deploy_storage_module(session: AsyncSession, user_id: int) -> dict:
    """Run Terraform plan + apply for the storage module.

    On success, reads outputs and stores bucket names plus Pub/Sub
    topic and subscription names in platform_config.
    This is the default implementation; tests mock this function.
    """
    from app.services.terraform_executor import TerraformExecutor

    run = await TerraformExecutor.run_plan(session, user_id, module_name="storage")
    await session.commit()

    if run.status != "awaiting_confirmation":
        return {"status": "failed", "message": run.error_message or "Plan failed"}

    # Fully consume the generator to avoid closing it mid-iteration, which
    # would trigger GeneratorExit while asyncpg still has an operation in
    # flight (run_apply flushes progress updates internally).
    error_message: str | None = None
    async for event in TerraformExecutor.run_apply(session, run.id, user_id):
        if event.event_type == "apply_error":
            error_message = event.message

    if error_message is not None:
        return {"status": "failed", "message": error_message}

    await session.commit()
    return {"status": "completed"}


@router.post(
    "/api/v1/infrastructure/storage/deploy",
    response_model=StorageDeployResponse,
)
async def deploy_storage(
    current_user: dict = require_permission("infrastructure", "view"),
    session: AsyncSession = Depends(get_session),
) -> StorageDeployResponse:
    """Deploy GCS storage buckets via Terraform."""
    user_id = int(current_user["sub"])

    # Read preconditions
    rows = (
        await session.execute(
            text("SELECT key, value FROM platform_config WHERE key IN ('terraform_initialized', 'storage_deployed')")
        )
    ).fetchall()
    config = {r[0]: r[1] for r in rows}

    if config.get("terraform_initialized", "false") != "true":
        raise HTTPException(
            status_code=400,
            detail="Terraform has not been initialized. Run bootstrap first.",
        )
    if config.get("storage_deployed", "false") == "true":
        raise HTTPException(
            status_code=409,
            detail="Storage infrastructure has already been deployed.",
        )

    result = await deploy_storage_module(session, user_id)
    return StorageDeployResponse(**result)


# ---------------------------------------------------------------------------
# Buckets endpoint (replaces the hardcoded one in infrastructure.py)
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/infrastructure/storage/buckets",
    response_model=BucketMetricsResponse,
)
async def get_storage_buckets(
    current_user: dict = require_permission("infrastructure", "view"),
    session: AsyncSession = Depends(get_session),
) -> BucketMetricsResponse:
    """Return live bucket metrics from the GCS API."""
    # Check deployment status
    row = (await session.execute(text("SELECT value FROM platform_config WHERE key = 'storage_deployed'"))).fetchone()

    if not row or row[0] != "true":
        raise HTTPException(
            status_code=400,
            detail="Storage infrastructure has not been deployed yet.",
        )

    metrics = await GcsStorageService.get_bucket_metrics(session)
    return BucketMetricsResponse(buckets=metrics)


# ---------------------------------------------------------------------------
# File assignment endpoints
# ---------------------------------------------------------------------------


@router.post("/api/v1/files/{file_id}/assign")
async def assign_file(
    file_id: int,
    body: FileAssignRequest,
    current_user: dict = require_permission("infrastructure", "configure"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Assign or reassign a file to an experiment."""
    user_id = int(current_user["sub"])
    try:
        await FileOrganizationService.assign_file_to_experiment(session, file_id, body.experiment_id, user_id)
    except ValueError as exc:
        if "not found" in str(exc).lower():
            raise HTTPException(status_code=404, detail=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok", "file_id": file_id, "experiment_id": body.experiment_id}


@router.post("/api/v1/files/{file_id}/unlink")
async def unlink_file(
    file_id: int,
    current_user: dict = require_permission("infrastructure", "configure"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Unlink a file from its experiment."""
    user_id = int(current_user["sub"])
    try:
        await FileOrganizationService.unlink_file_from_experiment(session, file_id, user_id)
    except ValueError as exc:
        if "not found" in str(exc).lower():
            raise HTTPException(status_code=404, detail=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok", "file_id": file_id}
