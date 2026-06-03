from fastapi import APIRouter, Depends

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.api.dependencies import require_permission
from app.adapters.registry import get_storage_adapter
from app.schemas.infrastructure import (
    InfraStorageMetricsResponse,
    ComputeStackResponse,
    BucketMetrics,
    ComponentDefinitionResponse,
    ComponentsListResponse,
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
    for bucket in metrics.get("buckets", []):
        buckets.append(
            BucketMetrics(
                name=bucket.get("name", "unknown"),
                size_gb=bucket.get("size_gb", 0.0),
                object_count=bucket.get("object_count", 0),
                storage_class=bucket.get("storage_class", "STANDARD"),
                cost_monthly_usd=bucket.get("cost_monthly_usd", 0.0),
            )
        )

    return InfraStorageMetricsResponse(
        buckets=buckets,
        total_size_gb=metrics.get("total_size_gb", 0.0),
        total_cost_monthly_usd=metrics.get("total_cost_monthly_usd", 0.0),
    )


@router.get("/compute/stack", response_model=ComputeStackResponse)
async def get_compute_stack(
    current_user: dict = require_permission("infrastructure", "view"),
    session: AsyncSession = Depends(get_session),
):
    """Returns the current compute stack selection."""
    result = await session.execute(text("SELECT value FROM platform_config WHERE key = 'compute_stack'"))
    row = result.first()
    return ComputeStackResponse(compute_stack=row[0] if row else "kubernetes")


@router.get("/components", response_model=ComponentsListResponse)
async def get_components(
    current_user: dict = require_permission("infrastructure", "view"),
    session: AsyncSession = Depends(get_session),
):
    """Returns component catalog filtered by active compute stack."""
    from app.services.component_service import COMPONENT_CATALOG

    result = await session.execute(text("SELECT value FROM platform_config WHERE key = 'compute_stack'"))
    row = result.first()
    compute_stack = row[0] if row else "kubernetes"

    # Components with no backend implementation yet, regardless of compute stack
    unimplemented = {"snakemake_k8s", "snakemake"}

    components = []
    for key, defn in COMPONENT_CATALOG.items():
        comp_stack = defn.get("compute_stack")
        if key in unimplemented:
            status = "coming_soon"
        elif compute_stack == "kubernetes":
            if comp_stack == "slurm":
                status = "coming_soon"
            else:
                status = "available"
        else:
            # slurm stack -- all adapters are stubbed so mark k8s as coming_soon
            if comp_stack == "kubernetes":
                status = "coming_soon"
            else:
                status = "coming_soon"

        components.append(
            ComponentDefinitionResponse(
                key=key,
                name=defn["name"],
                category=defn["category"],
                description=defn["description"],
                cost_estimate=defn.get("estimated_monthly_cost", ""),
                dependencies=defn.get("dependencies", []),
                configurable_fields=defn.get("config_schema", []),
                status=status,
            )
        )

    return ComponentsListResponse(compute_stack=compute_stack, components=components)


# NOTE: The /storage/buckets endpoint was moved to app/api/storage_deploy.py
# in Phase 18 to support live GCS bucket metrics.
