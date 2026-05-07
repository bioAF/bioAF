import logging
from datetime import datetime, timezone

from sqlalchemy import select, text
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
    async def _load_gcs_credentials(session: AsyncSession):
        """Load credentials suitable for project-level GCS list operations.

        Returns impersonated bootstrap credentials in vm_default mode (bootstrap
        has unconditioned roles/storage.admin), service_account.Credentials in
        legacy mode, or None on failure (caller falls back gracefully).
        """
        from app.services import credential_injector

        rows = await session.execute(
            text(
                "SELECT key, value FROM platform_config "
                "WHERE key IN ("
                "  'gcp_credential_source', 'gcp_service_account_key',"
                "  'gcp_service_account_email', 'gcp_bootstrap_sa_email'"
                ")"
            )
        )
        config = {r[0]: r[1] for r in rows.fetchall()}
        try:
            return credential_injector.load_gcp_credentials(config)
        except Exception as exc:
            logger.warning("Failed to load credentials for GCS bucket query: %s", exc)
            return None

    @staticmethod
    async def get_lifecycle_policies(session: AsyncSession, org_id: int) -> list[dict]:
        """Get lifecycle policy status from GCS buckets."""
        try:
            from google.cloud import storage as gcs_storage

            credentials = await StorageService._load_gcs_credentials(session)
            client = gcs_storage.Client(credentials=credentials) if credentials else gcs_storage.Client()
            prefix = f"bioaf-{org_id}-"
            policies = []
            for bucket in client.list_buckets(prefix=prefix):
                rules = []
                if bucket.lifecycle_rules:
                    for rule in bucket.lifecycle_rules:
                        rules.append(dict(rule))
                policies.append(
                    {
                        "bucket_name": bucket.name,
                        "rules": rules,
                        "enabled": len(rules) > 0,
                    }
                )
            return policies
        except Exception as e:
            logger.warning("Failed to get lifecycle policies: %s", e)
            return []

    @staticmethod
    async def _query_gcs_buckets(session: AsyncSession, org_id: int) -> list[dict]:
        """Query GCS for bucket stats. Falls back to empty list on failure."""
        try:
            from google.cloud import storage as gcs_storage

            credentials = await StorageService._load_gcs_credentials(session)
            client = gcs_storage.Client(credentials=credentials) if credentials else gcs_storage.Client()
            prefix = f"bioaf-{org_id}-"
            results = []
            for bucket in client.list_buckets(prefix=prefix):
                total_bytes = 0
                object_count = 0
                by_class: dict[str, int] = {}
                for blob in bucket.list_blobs():
                    total_bytes += blob.size or 0
                    object_count += 1
                    sc = blob.storage_class or "STANDARD"
                    by_class[sc] = by_class.get(sc, 0) + (blob.size or 0)
                results.append(
                    {
                        "name": bucket.name,
                        "total_bytes": total_bytes,
                        "object_count": object_count,
                        "by_storage_class": by_class,
                    }
                )
            return results
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
