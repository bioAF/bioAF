"""GCS Storage Service.

Wraps the google-cloud-storage Python client for bucket metrics,
file moves, and prefix management. All methods read bucket names
from platform_config.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from google.cloud import storage
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("bioaf.gcs_storage")


class BucketMetrics(BaseModel):
    bucket_name: str
    purpose: str
    size_bytes: int
    object_count: int
    storage_class: str
    versioning_enabled: bool
    lifecycle_rules: list[str]
    created_at: str | None = None


# Maps platform_config key to purpose label
_BUCKET_CONFIG_KEYS = {
    "ingest_bucket_name": "ingest",
    "raw_bucket_name": "raw",
    "working_bucket_name": "working",
    "results_bucket_name": "results",
    "config_backups_bucket_name": "config_backups",
}


class GcsStorageService:
    """GCS operations backed by platform_config bucket names."""

    @staticmethod
    async def get_bucket_metrics(session: AsyncSession) -> list[BucketMetrics]:
        """Query GCS API for each managed bucket and return metrics.

        NOTE: Listing all objects to compute size and count can be expensive
        for large buckets. For production, replace with GCS Storage Insights
        or a cached background job.
        """
        config = await GcsStorageService._read_storage_config(session)

        if config.get("storage_deployed", "false") != "true":
            raise ValueError("Storage infrastructure has not been deployed yet")

        credentials = await GcsStorageService.get_credentials(session)
        client = storage.Client(credentials=credentials)
        metrics: list[BucketMetrics] = []

        for config_key, purpose in _BUCKET_CONFIG_KEYS.items():
            bucket_name = config.get(config_key, "")
            if not bucket_name or bucket_name == "null":
                continue

            bucket = client.get_bucket(bucket_name)
            blobs = list(client.list_blobs(bucket_name))
            total_size = sum(b.size or 0 for b in blobs)

            lifecycle_summaries: list[str] = []
            for rule in bucket.lifecycle_rules or []:
                action = rule.get("action", {})
                condition = rule.get("condition", {})
                action_type = action.get("type", "")
                if action_type == "SetStorageClass":
                    target = action.get("storageClass", "")
                    age = condition.get("age", "?")
                    lifecycle_summaries.append(f"Transition to {target} after {age} days")
                elif action_type == "Delete":
                    age = condition.get("age", "?")
                    lifecycle_summaries.append(f"Delete after {age} days")

            created = str(bucket.time_created) if bucket.time_created else None

            metrics.append(
                BucketMetrics(
                    bucket_name=bucket_name,
                    purpose=purpose,
                    size_bytes=total_size,
                    object_count=len(blobs),
                    storage_class=bucket.storage_class or "STANDARD",
                    versioning_enabled=bool(bucket.versioning_enabled),
                    lifecycle_rules=lifecycle_summaries,
                    created_at=created,
                )
            )

        return metrics

    @staticmethod
    async def get_credentials(session: AsyncSession):
        """Return GCS credentials from platform_config, or None to use ADC.

        When gcp_credential_source is 'service_account_key', parses the stored
        JSON key and returns service_account.Credentials with full
        cloud-platform scope so the GCS client bypasses the VM's OAuth scopes.
        """
        import json as _json

        result = await session.execute(
            text(
                "SELECT key, value FROM platform_config "
                "WHERE key IN ('gcp_credential_source', 'gcp_service_account_key')"
            )
        )
        config = {r[0]: r[1] for r in result.fetchall()}

        if config.get("gcp_credential_source") != "service_account_key":
            return None

        key_json = config.get("gcp_service_account_key")
        if not key_json or key_json == "null":
            return None

        try:
            from google.oauth2 import service_account

            key_data = _json.loads(key_json)
            return service_account.Credentials.from_service_account_info(
                key_data,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
        except Exception as e:
            logger.warning("Failed to load GCS credentials from platform_config: %s", e)
            return None

    @staticmethod
    async def move_file(source_uri: str, destination_uri: str, credentials=None) -> str:
        """Copy an object from source to destination in GCS, then delete source.

        Returns the destination URI. If copy fails, source is NOT deleted (fail-safe).
        Pass credentials from get_credentials() for service account auth;
        omit to fall back to ADC.
        """
        src_bucket_name, src_blob_path = GcsStorageService._parse_gcs_uri(source_uri)
        dst_bucket_name, dst_blob_path = GcsStorageService._parse_gcs_uri(destination_uri)

        client = storage.Client(credentials=credentials)
        src_bucket = client.get_bucket(src_bucket_name)
        dst_bucket = client.get_bucket(dst_bucket_name)
        src_blob = src_bucket.blob(src_blob_path)

        # Copy first - if this fails, source is preserved
        dst_bucket.copy_blob(src_blob, dst_bucket, dst_blob_path)

        # Verify the copy succeeded before deleting
        dst_blob = dst_bucket.blob(dst_blob_path)
        if not dst_blob.exists():
            raise RuntimeError(f"Copy verification failed: {destination_uri} does not exist after copy")

        # Safe to delete source now
        src_blob.delete()

        return destination_uri

    @staticmethod
    def build_experiment_prefix(experiment_id: int) -> str:
        """Returns the GCS prefix for an experiment's files."""
        return f"experiments/{experiment_id}/"

    @staticmethod
    def build_unlinked_prefix() -> str:
        """Returns the GCS prefix for files not linked to an experiment."""
        return "unlinked/"

    @staticmethod
    def _parse_gcs_uri(uri: str) -> tuple[str, str]:
        """Parse gs://bucket/path into (bucket_name, blob_path)."""
        parsed = urlparse(uri)
        bucket = parsed.netloc
        path = parsed.path.lstrip("/")
        return bucket, path

    @staticmethod
    async def _read_storage_config(session: AsyncSession) -> dict[str, str]:
        """Read storage-related keys from platform_config."""
        keys = [
            "storage_deployed",
            "ingest_bucket_name",
            "raw_bucket_name",
            "working_bucket_name",
            "results_bucket_name",
            "config_backups_bucket_name",
        ]
        rows = (
            await session.execute(
                text("SELECT key, value FROM platform_config WHERE key = ANY(:keys)").bindparams(keys=keys)
            )
        ).fetchall()
        return {r[0]: r[1] for r in rows}
