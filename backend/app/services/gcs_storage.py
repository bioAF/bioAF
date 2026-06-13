"""GCS bucket-metrics + credential helper (legacy).

After Phase 3 of the BAL rework, all object-store operations (upload/download/
move/read/delete/list/signed-URL) go through ``get_storage_adapter()``; this
module no longer performs object I/O. After Phase 9 (Stage 3b) it no longer
performs bucket I/O either. What remains is support-only:

  - ``get_bucket_metrics``: maps the storage adapter's neutral
    ``get_bucket_admin_metrics`` onto the per-purpose ``BucketMetrics`` the
    Components view renders. The google-cloud-storage walk now lives in the
    adapter; this service holds no cloud SDK.
  - ``get_credentials``: GCP credential resolution used by a few non-storage
    callers (terraform subprocess, pubsub). Routes through
    ``app.adapters.credentials.credential_injector`` (no cloud SDK here).
  - ``build_*_prefix`` / ``_parse_gcs_uri``: pure path helpers (no SDK).
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

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
        """Return per-managed-bucket metrics for the Components view.

        The bucket enumeration runs in the storage adapter (cloud-selected);
        this method maps the adapter's neutral ``BucketAdminMetrics`` onto the
        per-purpose ``BucketMetrics`` the UI renders.

        NOTE: Listing all objects to compute size and count can be expensive
        for large buckets. For production, replace with GCS Storage Insights
        or a cached background job.
        """
        from app.adapters.registry import get_storage_adapter

        config = await GcsStorageService._read_storage_config(session)

        if config.get("storage_deployed", "false") != "true":
            raise ValidationError("Storage infrastructure has not been deployed yet")

        adapter = get_storage_adapter()
        metrics: list[BucketMetrics] = []

        for config_key, purpose in _BUCKET_CONFIG_KEYS.items():
            bucket_name = config.get(config_key, "")
            if not bucket_name or bucket_name == "null":
                continue

            admin = await adapter.get_bucket_admin_metrics(bucket_name)

            metrics.append(
                BucketMetrics(
                    bucket_name=bucket_name,
                    purpose=purpose,
                    size_bytes=admin.size_bytes,
                    object_count=admin.object_count,
                    storage_class=admin.storage_class,
                    versioning_enabled=admin.versioning_enabled,
                    lifecycle_rules=admin.lifecycle_summaries,
                    created_at=admin.created_at,
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
        from app.adapters.credentials import credential_injector
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
        """Parse a storage URI (scheme://bucket/path) into (bucket_name, blob_path)."""
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
