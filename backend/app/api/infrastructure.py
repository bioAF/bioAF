from fastapi import APIRouter, Depends

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.api.dependencies import require_permission
from app.adapters.registry import get_storage_adapter
from app.platform.cloud_provider import get_cloud_provider
from app.platform.platform_config_service import PlatformConfigService
from app.platform.stack_options import stack_options_for
from app.schemas.infrastructure import (
    InfraStorageMetricsResponse,
    ComputeStackResponse,
    BucketMetrics,
    StackOptionsResponse,
)

router = APIRouter(prefix="/api/v1/infrastructure", tags=["infrastructure"])


@router.get("/storage/metrics", response_model=InfraStorageMetricsResponse)
async def get_storage_metrics(
    current_user: dict = require_permission("infrastructure", "view"),
):
    """Returns storage metrics from the active storage adapter."""
    storage_adapter = get_storage_adapter()
    metrics = await storage_adapter.get_storage_metrics()

    buckets = []
    for bucket in metrics.buckets:
        buckets.append(
            BucketMetrics(
                name=bucket.name or "unknown",
                size_gb=bucket.size_gb or 0.0,
                object_count=bucket.object_count or 0,
                storage_class=bucket.storage_class or "STANDARD",
                cost_monthly_usd=bucket.cost_monthly_usd or 0.0,
            )
        )

    return InfraStorageMetricsResponse(
        buckets=buckets,
        total_size_gb=metrics.total_size_gb,
        total_cost_monthly_usd=metrics.total_cost_monthly_usd,
    )


@router.get("/compute/stack", response_model=ComputeStackResponse)
async def get_compute_stack(
    current_user: dict = require_permission("infrastructure", "view"),
    session: AsyncSession = Depends(get_session),
):
    """Returns the current compute stack selection."""
    compute_stack = await PlatformConfigService.get(session, "compute_stack") or "kubernetes"
    return ComputeStackResponse(compute_stack=compute_stack)


@router.get("/stack-options", response_model=StackOptionsResponse)
async def get_stack_options(
    current_user: dict = require_permission("infrastructure", "view"),
    session: AsyncSession = Depends(get_session),
):
    """Valid compute+storage stack options for this install's cloud_provider.

    Reads the cloud-provider policy (the same source the BAL resolves backends
    from) so the setup UI presents provider-appropriate options: GCP -> GKE+GCS /
    SLURM+NFS, AWS -> EKS+S3 / SLURM+NFS. Behavior-preserving on GCP.
    """
    cloud_provider = await get_cloud_provider(session)
    return StackOptionsResponse(
        cloud_provider=cloud_provider,
        options=stack_options_for(cloud_provider),
    )


# NOTE: The /storage/buckets endpoint was moved to app/api/storage_deploy.py
# in Phase 18 to support live GCS bucket metrics.
# NOTE: The component-catalog endpoint (GET /components) was removed; the live
# component list is served by /api/v1/infrastructure/stack/components.
