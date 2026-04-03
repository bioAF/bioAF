from fastapi import APIRouter, Depends

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.api.dependencies import require_permission
from app.adapters.registry import get_compute_adapter, get_storage_adapter
from app.schemas.infrastructure import (
    InfraComputeStatusResponse,
    InfraComputeMetricsResponse,
    InfraStorageMetricsResponse,
    ComputeStackResponse,
    NodePoolStatus,
    NodePoolMetrics,
    BucketMetrics,
    ComponentDefinitionResponse,
    ComponentsListResponse,
)

router = APIRouter(prefix="/api/v1/infrastructure", tags=["infrastructure"])


@router.get("/compute/status", response_model=InfraComputeStatusResponse)
async def get_compute_status(
    current_user: dict = require_permission("infrastructure", "change_status"),
):
    """Returns cluster status from the active compute adapter."""
    compute_adapter = get_compute_adapter()
    status = await compute_adapter.get_cluster_status()

    node_pools = []
    for pool in status.get("node_pools", []):
        node_pools.append(
            NodePoolStatus(
                name=pool.get("name", "unknown"),
                machine_type=pool.get("machine_type", "unknown"),
                min_nodes=pool.get("min_nodes", 0),
                max_nodes=pool.get("max_nodes", 0),
                current_nodes=pool.get("current_nodes", 0),
                status=pool.get("status", "unknown"),
                spot=pool.get("spot", False),
            )
        )

    return InfraComputeStatusResponse(
        controller_status=status.get("controller_status", "unknown"),
        node_pools=node_pools,
        total_nodes=status.get("total_nodes", 0),
        active_nodes=status.get("active_nodes", 0),
        queue_depth=status.get("queue_depth", 0),
        health=status.get("health", "unknown"),
    )


@router.get("/compute/metrics", response_model=InfraComputeMetricsResponse)
async def get_compute_metrics(
    current_user: dict = require_permission("infrastructure", "view"),
):
    """Returns cluster metrics from the active compute adapter."""
    compute_adapter = get_compute_adapter()
    metrics = await compute_adapter.get_cluster_metrics()

    node_pools = []
    for pool in metrics.get("node_pools", []):
        node_pools.append(
            NodePoolMetrics(
                name=pool.get("name", "unknown"),
                cpu_utilization_pct=pool.get("cpu_utilization_pct", 0.0),
                memory_utilization_pct=pool.get("memory_utilization_pct", 0.0),
                cost_rate_hourly=pool.get("cost_rate_hourly", 0.0),
            )
        )

    return InfraComputeMetricsResponse(
        cpu_utilization_pct=metrics.get("cpu_utilization_pct", 0.0),
        memory_utilization_pct=metrics.get("memory_utilization_pct", 0.0),
        cost_burn_rate_hourly=metrics.get("cost_burn_rate_hourly", 0.0),
        node_pools=node_pools,
    )


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
