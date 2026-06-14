"""Tests for GCS Storage Service.

Tests:
6. get_bucket_metrics returns all 7 buckets
7. get_bucket_metrics requires storage_deployed
8. move_file copies then deletes source
9. move_file does NOT delete on copy failure
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.adapters.models import BucketAdminMetrics
from app.exceptions import ValidationError
from sqlalchemy import text


@pytest.mark.asyncio
async def test_get_bucket_metrics_returns_all_buckets(session):
    """Mock GCS client. Assert all 7 bucket metrics returned with correct purposes.

    references and literature must appear: a partial read-key list previously
    dropped them from the Components view even when their names were persisted.
    """
    # Seed platform_config with deployed state and bucket names
    for key, value in [
        ("storage_deployed", "true"),
        ("ingest_bucket_name", "bioaf-ingest-demo"),
        ("raw_bucket_name", "bioaf-raw-demo"),
        ("working_bucket_name", "bioaf-working-demo"),
        ("results_bucket_name", "bioaf-results-demo"),
        ("references_bucket_name", "bioaf-references-demo"),
        ("literature_bucket_name", "bioaf-literature-demo"),
        ("config_backups_bucket_name", "bioaf-config-backups-demo"),
    ]:
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=key, v=value)
        )
    await session.commit()

    # The bucket walk now lives in the storage adapter; stub its neutral result
    # so the service's per-purpose mapping is what's under test.
    admin = BucketAdminMetrics(
        size_bytes=1024,
        object_count=1,
        storage_class="STANDARD",
        versioning_enabled=True,
        lifecycle_summaries=["Delete after 365 days"],
        created_at="2026-03-11T00:00:00Z",
    )

    with patch(
        "app.adapters.storage.gcs.GcsStorageProvider.get_bucket_admin_metrics",
        AsyncMock(return_value=admin),
    ):
        from app.services.gcs_storage import GcsStorageService

        metrics = await GcsStorageService.get_bucket_metrics(session)

    assert len(metrics) == 7
    purposes = {m.purpose for m in metrics}
    assert purposes == {
        "ingest",
        "raw",
        "working",
        "results",
        "references",
        "literature",
        "config_backups",
    }
    # The adapter's neutral fields map straight onto the rendered BucketMetrics.
    sample = metrics[0]
    assert sample.size_bytes == 1024
    assert sample.versioning_enabled is True
    assert sample.lifecycle_rules == ["Delete after 365 days"]
    assert sample.created_at == "2026-03-11T00:00:00Z"


@pytest.mark.asyncio
async def test_get_bucket_metrics_requires_deployed(session):
    """Call when storage_deployed is false. Assert error raised."""
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES ('storage_deployed', 'false') "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    )
    await session.commit()

    from app.services.gcs_storage import GcsStorageService

    with pytest.raises(ValidationError, match="not been deployed"):
        await GcsStorageService.get_bucket_metrics(session)


# NOTE: move_file / read_object_text were removed from GcsStorageService in
# Phase 3 of the BAL rework; object I/O now goes through the storage adapter.
# The fail-safe copy-verify-delete move behavior is covered by
# test_gcs_object_store.py (test_move_is_failsafe_on_partial_failure and
# test_move_deletes_source_after_verified_copy).
