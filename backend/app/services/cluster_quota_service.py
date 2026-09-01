"""Assemble a node-pool quota preflight from live cloud numbers.

`cluster_quota` decides; this fetches what it decides against. Kept separate so
the arithmetic stays pure and testable against the exact figures the pd-balanced
incident produced, while the I/O (cloud quota read, live node count, config
lookup) lives here.

Everything degrades rather than raises. A backend with no quota reader, a service
account without `compute.regions.get`, or an unreachable cluster all produce an
"unverified" verdict, never a blocked operator.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.registry import get_compute_adapter
from app.services.cluster_quota import (
    PoolPlan,
    QuotaVerdict,
    evaluate_pool_quota,
    per_node_costs,
)
from app.platform.platform_config_service import PlatformConfigService

logger = logging.getLogger(__name__)

# The GKE node pool the pipeline workload runs on. Its name is fixed by the
# compute terraform module.
PIPELINE_POOL_NAME = "bioaf-pipelines"

_PIPELINE_KEYS = [
    "k8s_pipeline_machine_type",
    "k8s_pipeline_max_nodes",
    "k8s_pipeline_use_spot",
    "k8s_pipeline_disk_size_gb",
    "k8s_pipeline_disk_type",
]


def _plan_from(config: dict, overrides: dict | None = None) -> PoolPlan:
    """Build a pool shape from stored config, with optional proposed overrides.

    Defaults mirror the terraform variables so an install that never wrote these
    rows is priced as what its pool actually runs, not as zero.
    """
    merged = {**config, **{k: v for k, v in (overrides or {}).items() if v is not None}}

    def _int(key: str, default: int) -> int:
        try:
            return int(merged.get(key) or default)
        except (TypeError, ValueError):
            return default

    use_spot = merged.get("k8s_pipeline_use_spot")
    if isinstance(use_spot, str):
        use_spot = use_spot == "true"

    return PoolPlan(
        machine_type=str(merged.get("k8s_pipeline_machine_type") or "n2-highmem-16"),
        max_nodes=_int("k8s_pipeline_max_nodes", 20),
        disk_size_gb=_int("k8s_pipeline_disk_size_gb", 100),
        disk_type=str(merged.get("k8s_pipeline_disk_type") or "pd-standard"),
        use_spot=bool(use_spot if use_spot is not None else True),
    )


class ClusterQuotaService:
    """Preflight a pipeline-pool config against the region's live quota."""

    @staticmethod
    async def evaluate_pipeline_pool(
        session: AsyncSession,
        proposed: dict | None = None,
    ) -> QuotaVerdict:
        """Verdict for the pipeline pool, optionally under a proposed change.

        With `proposed` omitted this reports what the CURRENT pool can build,
        which is what the Components page shows: terraform reporting success and
        GKE reporting RUNNING were both true of a pool that could not create an
        instance, and this is the number that tells them apart.
        """
        config = await PlatformConfigService.get_many(session, _PIPELINE_KEYS)

        try:
            adapter = get_compute_adapter()
        except Exception as exc:
            logger.debug("No compute adapter for quota preflight: %s", exc)
            return evaluate_pool_quota(_plan_from(config, proposed), None)

        quotas = None
        node_count = None
        try:
            quotas = await adapter.get_regional_quotas()
            if quotas is not None:
                node_count = await adapter.count_pool_nodes(PIPELINE_POOL_NAME)
        except Exception as exc:
            # The adapter is contracted to swallow its own failures; this is the
            # belt-and-braces path for a backend that does not honour that.
            logger.debug("Quota preflight could not read the cloud: %s", exc)
            quotas = None

        current_usage: dict[str, float] = {}
        if quotas is not None and node_count:
            # Price the pool's EXISTING shape, not the proposed one: those are the
            # nodes currently drawing on the quota. Netting them out is what stops
            # an operator being blocked by their own running pool.
            current_plan = _plan_from(config)
            current_usage = {metric: cost * node_count for metric, cost in per_node_costs(current_plan, quotas).items()}

        return evaluate_pool_quota(_plan_from(config, proposed), quotas, current_usage)
