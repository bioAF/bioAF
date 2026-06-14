import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.storage_stats import StorageStatsCache

logger = logging.getLogger("bioaf.storage_service")

# Cost per GB per month by storage class
STORAGE_COSTS = {
    "STANDARD": 0.020,
    "NEARLINE": 0.010,
    "COLDLINE": 0.004,
    "ARCHIVE": 0.0012,
}


class StorageService:
    @staticmethod
    async def get_storage_stats(session: AsyncSession, org_id: int) -> dict:
        """Get storage stats, using cache if <1h old."""
        result = await session.execute(
            select(StorageStatsCache)
            .where(StorageStatsCache.organization_id == org_id)
            .order_by(StorageStatsCache.computed_at.desc())
            .limit(1)
        )
        cached = result.scalar_one_or_none()

        if cached:
            age = (datetime.now(timezone.utc) - cached.computed_at.replace(tzinfo=timezone.utc)).total_seconds()
            if age < 3600:
                return cached.stats_json

        # Refresh from GCS
        return await StorageService.refresh_storage_stats(session, org_id)

    @staticmethod
    async def refresh_storage_stats(session: AsyncSession, org_id: int) -> dict:
        """Query GCS API for bucket stats and cache results."""
        buckets_data = await StorageService._query_gcs_buckets(session, org_id)

        total_bytes = 0
        total_cost = 0.0
        buckets = []

        for bucket_info in buckets_data:
            bucket_cost = 0.0
            for storage_class, bytes_val in bucket_info.get("by_storage_class", {}).items():
                gb = bytes_val / (1024**3)
                rate = STORAGE_COSTS.get(storage_class.upper(), STORAGE_COSTS["STANDARD"])
                bucket_cost += gb * rate

            bucket_stat = {
                "bucket_name": bucket_info["name"],
                "total_bytes": bucket_info.get("total_bytes", 0),
                "object_count": bucket_info.get("object_count", 0),
                "by_storage_class": bucket_info.get("by_storage_class", {}),
                "cost_estimate_monthly": round(bucket_cost, 2),
            }
            buckets.append(bucket_stat)
            total_bytes += bucket_stat["total_bytes"]
            total_cost += bucket_cost

        stats = {
            "buckets": buckets,
            "total_bytes": total_bytes,
            "total_cost_estimate_monthly": round(total_cost, 2),
            "lifecycle_policies": [],
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

        # Cache the result
        cache_entry = StorageStatsCache(
            organization_id=org_id,
            bucket_name="aggregate",
            stats_json=stats,
        )
        session.add(cache_entry)
        await session.flush()

        return stats

    @staticmethod
    async def get_lifecycle_policies(session: AsyncSession, org_id: int) -> list[dict]:
        """Get lifecycle policy status from the org's buckets.

        The cloud-selected storage adapter owns the bucket walk (and resolves the
        impersonated/legacy credentials it needs); this service only scopes the
        prefix and keeps the graceful empty-list fallback.
        """
        from app.adapters.registry import get_storage_adapter

        try:
            return await get_storage_adapter().list_lifecycle_policies(f"bioaf-{org_id}-")
        except Exception as e:
            logger.warning("Failed to get lifecycle policies: %s", e)
            return []

    @staticmethod
    async def _query_gcs_buckets(session: AsyncSession, org_id: int) -> list[dict]:
        """Query the org's buckets for stats. Falls back to a placeholder on failure.

        The cloud-selected storage adapter owns the bucket walk (and resolves the
        impersonated/legacy credentials it needs); this service only scopes the
        prefix and keeps the graceful fallback.
        """
        from app.adapters.registry import get_storage_adapter

        try:
            return await get_storage_adapter().query_bucket_stats(f"bioaf-{org_id}-")
        except Exception as e:
            logger.warning("GCS bucket query failed: %s", e)
            return [
                {
                    "name": f"bioaf-{org_id}-data",
                    "total_bytes": 0,
                    "object_count": 0,
                    "by_storage_class": {},
                }
            ]
