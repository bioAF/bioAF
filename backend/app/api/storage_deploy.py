"""Phase 18 Storage infrastructure endpoints.

- POST /api/v1/infrastructure/storage/deploy  - deploy GCS buckets via Terraform
- GET  /api/v1/infrastructure/storage/buckets  - live bucket metrics (replaces hardcoded)
- POST /api/v1/files/{file_id}/assign          - assign file to experiment
- POST /api/v1/files/{file_id}/unlink          - unlink file from experiment
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_any_permission, require_permission
from app.database import get_session
from app.platform.platform_config_service import PlatformConfigService
from app.services.deployment_outputs import STORAGE_DEPLOY_OUTPUTS, BlankOutputs, persist_outputs
from app.services.file_organization import FileOrganizationService
from app.services.gcs_storage import BucketMetrics, GcsStorageService

logger = logging.getLogger("bioaf.storage_deploy_api")


async def _store_storage_outputs(session: AsyncSession) -> None:
    """Read Terraform outputs for the storage module and store them in platform_config."""
    from app.services.terraform_executor import TerraformExecutor

    try:
        outputs = await TerraformExecutor.read_module_outputs(session, "storage")
    except Exception:
        logger.warning("Could not read storage module outputs after apply", exc_info=True)
        return

    await persist_outputs(session, outputs, STORAGE_DEPLOY_OUTPUTS, BlankOutputs.SKIP)
    await session.commit()


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
    from app.services.storage_generation import ensure_storage_generation
    from app.services.terraform_executor import TerraformExecutor

    # A first deploy (or one after a confirmed destroy) needs its storage
    # generation persisted before Terraform names anything.
    if await PlatformConfigService.get(session, "storage_deployed") != "true":
        await ensure_storage_generation(session)
        await session.commit()

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


async def _storage_operation(
    session: AsyncSession, current_user: dict, action: str, *, store_outputs: bool = False
) -> dict:
    """Plan, apply and record outputs (and the storage generation) as one accepted operation."""
    from app.services import infrastructure_operations as ops

    op_id, _ = await ops.accept(action=action, principal=ops.Principal.from_user(current_user))
    async with ops.owned(op_id):
        result = await deploy_storage_module(session, int(current_user["sub"]))
        # Store outputs since this apply may have added new resources (Pub/Sub).
        if store_outputs and result.get("status") == "completed":
            await _store_storage_outputs(session)
    await ops.finish(
        op_id,
        ops.succeeded("Storage applied.")
        if result.get("status") == "completed"
        else ops.failed("Applying storage failed.", "apply_failed"),
    )
    return result


@router.post(
    "/api/v1/infrastructure/storage/deploy",
    response_model=StorageDeployResponse,
)
async def deploy_storage(
    current_user: dict = require_permission("infrastructure", "deploy"),
    session: AsyncSession = Depends(get_session),
) -> StorageDeployResponse:
    """Deploy GCS storage buckets via Terraform."""

    # Read preconditions
    config = await PlatformConfigService.get_many(session, ["terraform_initialized", "storage_deployed"])

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

    result = await _storage_operation(session, current_user, "storage_deploy")
    return StorageDeployResponse(**result)


@router.post(
    "/api/v1/infrastructure/storage/update",
    response_model=StorageDeployResponse,
)
async def update_storage(
    current_user: dict = require_permission("infrastructure", "deploy"),
    session: AsyncSession = Depends(get_session),
) -> StorageDeployResponse:
    """Re-apply the storage module to update infrastructure (e.g. add Pub/Sub).

    Unlike /deploy, this endpoint works when storage is already deployed.
    Terraform will only create or modify resources that have changed.
    """

    config = await PlatformConfigService.get_many(session, ["terraform_initialized", "storage_deployed"])

    if config.get("terraform_initialized", "false") != "true":
        raise HTTPException(
            status_code=400,
            detail="Terraform has not been initialized. Run bootstrap first.",
        )
    if config.get("storage_deployed", "false") != "true":
        raise HTTPException(
            status_code=400,
            detail="Storage has not been deployed yet. Use /deploy instead.",
        )

    result = await _storage_operation(session, current_user, "storage_update", store_outputs=True)
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
    storage_deployed = await PlatformConfigService.get(session, "storage_deployed")

    if storage_deployed != "true":
        raise HTTPException(
            status_code=400,
            detail="Storage infrastructure has not been deployed yet.",
        )

    metrics = await GcsStorageService.get_bucket_metrics(session)
    return BucketMetricsResponse(buckets=metrics)


# ---------------------------------------------------------------------------
# Storage health and bucket confirmation
# ---------------------------------------------------------------------------


StoreHealthValue = Literal[
    "available",
    "not_found",
    "no_access",
    "not_responding",
    "not_confirmed",
    "wrong_owner",
    "not_mounted",
    "cannot_check",
]


class StoreHealthItem(BaseModel):
    store: str
    bucket_name: str
    health: StoreHealthValue
    # How identity was established: "discovered", "enrolled", or null when it was not.
    basis: str | None = None
    # What the provider reported at the last check: GCS project number or S3 owner, and creation time.
    observed_owner: str | None = None
    observed_created_at: datetime | None = None


class StorageHealthResponse(BaseModel):
    checked_at: datetime | None = None
    stores: list[StoreHealthItem]
    unconfirmed: int = 0


class ConfirmBucketsRequest(BaseModel):
    stores: list[str] = Field(min_length=1, max_length=16)
    reason: str = Field(min_length=1, max_length=2000)
    confirm: bool = False


def _health_response(reports) -> StorageHealthResponse:
    checked = [r.checked_at for r in reports if r.checked_at is not None]
    return StorageHealthResponse(
        checked_at=min(checked) if checked else None,
        stores=[
            StoreHealthItem(
                store=r.store,
                bucket_name=r.bucket_name,
                health=r.health.value,
                basis=r.basis,
                observed_owner=r.observed_owner,
                observed_created_at=r.observed_created_at,
            )
            for r in reports
        ],
        unconfirmed=sum(1 for r in reports if r.health.value == "not_confirmed"),
    )


@router.get("/api/v1/infrastructure/storage/health", response_model=StorageHealthResponse)
async def get_storage_health(
    current_user: dict = require_permission("infrastructure", "view"),
    session: AsyncSession = Depends(get_session),
) -> StorageHealthResponse:
    """The last read-only inspection of each configured bucket; inspects once if none is recorded."""
    from app.services import storage_identity

    reports = await storage_identity.latest_reports(session)
    configured = await storage_identity.configured_store_count(session)
    if len(reports) < configured:
        reports = await storage_identity.inspect_stores(session)
        await session.commit()
    return _health_response(reports)


@router.post("/api/v1/infrastructure/storage/health/check", response_model=StorageHealthResponse)
async def check_storage_health(
    current_user: dict = require_any_permission([("infrastructure", "deploy"), ("infrastructure", "configure")]),
    session: AsyncSession = Depends(get_session),
) -> StorageHealthResponse:
    """Inspect every configured bucket now. Read-only: nothing is created or changed in the cloud."""
    from app.services import storage_identity

    reports = await storage_identity.inspect_stores(session)
    await session.commit()
    return _health_response(reports)


@router.post("/api/v1/infrastructure/storage/identity/confirm", response_model=StorageHealthResponse)
async def confirm_storage_buckets(
    body: ConfirmBucketsRequest,
    current_user: dict = require_permission("infrastructure", "configure"),
    session: AsyncSession = Depends(get_session),
) -> StorageHealthResponse:
    """Record an administrator's confirmation that the observed buckets belong to this installation."""
    from app.services import storage_identity

    if not body.confirm:
        raise HTTPException(status_code=400, detail="Confirm that these buckets belong to this installation.")
    await storage_identity.enroll_stores(session, int(current_user["sub"]), body.stores, reason=body.reason)
    await session.commit()
    return _health_response(await storage_identity.latest_reports(session))


# ---------------------------------------------------------------------------
# Storage write admission (D5, mockups M7 and M11)
# ---------------------------------------------------------------------------


class StorageWritesResponse(BaseModel):
    # New uploads are refused until an administrator allows them or storage destroy runs again.
    paused: bool
    since: datetime | None = None
    operation_id: int | None = None


async def _writes_response(session: AsyncSession) -> StorageWritesResponse:
    from app.services.storage_write_admission import paused

    state = await paused(session)
    if state is None:
        return StorageWritesResponse(paused=False)
    since = state.get("since")
    return StorageWritesResponse(
        paused=True,
        since=datetime.fromisoformat(since) if since else None,
        operation_id=state.get("operation_id"),
    )


@router.get("/api/v1/infrastructure/storage/writes", response_model=StorageWritesResponse)
async def storage_writes_status(
    current_user: dict = require_permission("infrastructure", "view"),
    session: AsyncSession = Depends(get_session),
) -> StorageWritesResponse:
    return await _writes_response(session)


@router.post("/api/v1/infrastructure/storage/writes/allow", response_model=StorageWritesResponse)
async def allow_storage_writes(
    current_user: dict = require_permission("infrastructure", "configure"),
    session: AsyncSession = Depends(get_session),
) -> StorageWritesResponse:
    """Reopen uploads after a refused or failed storage destroy, without destroying anything (Allow uploads)."""
    from app.services import infrastructure_operations as ops
    from app.services.storage_write_admission import allow_writes

    active = await ops.active_operation(session)
    if active is not None and active.action == "storage_destroy":
        raise HTTPException(status_code=409, detail="Uploads cannot be allowed while storage destroy runs.")
    await allow_writes(session, int(current_user["sub"]))
    await session.commit()
    return await _writes_response(session)


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
        logger.warning("File assign failed for file %d: %s", file_id, exc)
        if "not found" in str(exc).lower():
            raise HTTPException(status_code=404, detail="File or experiment not found")
        raise HTTPException(status_code=400, detail="Failed to assign file")
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
        logger.warning("File unlink failed for file %d: %s", file_id, exc)
        if "not found" in str(exc).lower():
            raise HTTPException(status_code=404, detail="File not found")
        raise HTTPException(status_code=400, detail="Failed to unlink file")
    return {"status": "ok", "file_id": file_id}
