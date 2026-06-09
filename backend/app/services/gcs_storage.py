"""GCS bucket-metrics + credential helper (legacy).

After Phase 3 of the BAL rework, all object-store operations (upload/download/
move/read/delete/list/signed-URL) go through ``get_storage_adapter()``; this
module no longer performs object I/O. What remains is intentionally Tier-2 /
support-only and drains in Phase 9:

  - ``get_bucket_metrics``: per-bucket size/lifecycle/versioning enumeration
    (bucket-level admin metrics; the owner scoped bucket lifecycle/enumeration
    to Phase 9, so it keeps the google-cloud-storage import here).
  - ``get_credentials``: GCP credential resolution used by get_bucket_metrics
    and a few non-storage callers (terraform subprocess, pubsub). This belongs
    in ``app.platform.credential_injector``; relocate when Phase 9 lands.
  - ``build_*_prefix`` / ``_parse_gcs_uri``: pure path helpers (no SDK).
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from google.cloud import storage
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ValidationError

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
    "references_bucket_name": "references",
    "literature_bucket_name": "literature",
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
            raise ValidationError("Storage infrastructure has not been deployed yet")

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
        """Return credentials capable of v4 signing for GCS operations.

        Routes through credential_injector so vm_default installs get
        impersonated bootstrap credentials (which sign via the IAM
        SignBlob API) and legacy service_account_key installs get
        service_account.Credentials with full cloud-platform scope.
        Returns None on failure -- caller falls back to ADC, which
        works for non-signing operations only.
        """
        from app.platform import credential_injector
        from app.platform.platform_config_service import PlatformConfigService

        config = await PlatformConfigService.get_many(
            session,
            [
                "gcp_credential_source",
                "gcp_service_account_key",
                "gcp_service_account_email",
                "gcp_bootstrap_sa_email",
            ],
        )

        try:
            return credential_injector.load_gcp_credentials(config)
        except Exception as e:
            logger.warning("Failed to load GCS credentials from platform_config: %s", e)
            return None

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
        """Read storage-related keys from platform_config.

        The bucket-name keys are derived from _BUCKET_CONFIG_KEYS so the read
        path can never drift from the set get_bucket_metrics iterates over. A
        partial list here is what previously hid the references and literature
        buckets from the Components view even after their names were persisted.
        """
        from app.platform.platform_config_service import PlatformConfigService

        keys = ["storage_deployed", *_BUCKET_CONFIG_KEYS.keys()]
        return await PlatformConfigService.get_many(session, keys)
