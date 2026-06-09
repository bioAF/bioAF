"""Tests for the infrastructure components and storage buckets API endpoints."""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from app.adapters import registry


@pytest.fixture(autouse=True)
def init_adapters(monkeypatch):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "local")
    registry.reset_registry()
    registry.initialize_adapters_sync("kubernetes")
    yield
    registry.reset_registry()


class TestComponentsEndpoint:
    @pytest.mark.asyncio
    async def test_components_endpoint_removed(self, client, admin_token):
        """GET /api/v1/infrastructure/components was orphaned (no caller) and
        removed. The live component list is served by
        /api/v1/infrastructure/stack/components."""
        response = await client.get(
            "/api/v1/infrastructure/components",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 404

    def test_components_have_no_per_component_config(self):
        """Components are enable/disable only; they carry no per-component config.
        Per-component config_schema was vestigial (only the removed
        /api/v1/infrastructure/components endpoint ever surfaced it). Infra config
        (compute/storage) lives on the cluster-config endpoint, not on components.
        """
        from app.services.component_service import ComponentService

        offenders = [key for key, defn in ComponentService.get_catalog().items() if "config_schema" in defn]
        assert not offenders, f"components still carry a vestigial config_schema: {offenders}"


class TestStorageBucketsEndpoint:
    """Tests for GET /api/v1/infrastructure/storage/buckets.

    Phase 18 changed this endpoint to require storage_deployed=true and
    return live BucketMetrics from the GCS storage service.
    """

    @pytest.mark.asyncio
    async def test_returns_400_when_not_deployed(self, client, admin_token, session):
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES ('storage_deployed', 'false') "
                "ON CONFLICT (key) DO UPDATE SET value = 'false'"
            )
        )
        await session.commit()

        response = await client.get(
            "/api/v1/infrastructure/storage/buckets",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_returns_all_buckets_when_deployed(self, client, admin_token, session):
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

        from app.services.gcs_storage import BucketMetrics

        mock_metrics = [
            BucketMetrics(
                bucket_name=f"bioaf-{p}-demo",
                purpose=p,
                size_bytes=1024,
                object_count=5,
                storage_class="STANDARD",
                versioning_enabled=True,
                lifecycle_rules=[],
            )
            for p in [
                "ingest",
                "raw",
                "working",
                "results",
                "references",
                "literature",
                "config_backups",
            ]
        ]

        with patch("app.api.storage_deploy.GcsStorageService") as mock_svc:
            mock_svc.get_bucket_metrics = AsyncMock(return_value=mock_metrics)
            response = await client.get(
                "/api/v1/infrastructure/storage/buckets",
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert response.status_code == 200
        data = response.json()
        assert "buckets" in data
        assert len(data["buckets"]) == 7
        purposes = {b["purpose"] for b in data["buckets"]}
        assert "references" in purposes
        assert "literature" in purposes

    @pytest.mark.asyncio
    async def test_each_bucket_has_required_fields(self, client, admin_token, session):
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

        from app.services.gcs_storage import BucketMetrics

        mock_metrics = [
            BucketMetrics(
                bucket_name=f"bioaf-{p}-demo",
                purpose=p,
                size_bytes=2048,
                object_count=10,
                storage_class="STANDARD",
                versioning_enabled=True,
                lifecycle_rules=[],
            )
            for p in [
                "ingest",
                "raw",
                "working",
                "results",
                "references",
                "literature",
                "config_backups",
            ]
        ]

        with patch("app.api.storage_deploy.GcsStorageService") as mock_svc:
            mock_svc.get_bucket_metrics = AsyncMock(return_value=mock_metrics)
            response = await client.get(
                "/api/v1/infrastructure/storage/buckets",
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert response.status_code == 200
        data = response.json()

        for bucket in data["buckets"]:
            assert "bucket_name" in bucket
            assert "purpose" in bucket
            assert "size_bytes" in bucket
            assert "object_count" in bucket
            assert "storage_class" in bucket

    @pytest.mark.asyncio
    async def test_requires_admin_or_comp_bio_role(self, client, viewer_token):
        response = await client.get(
            "/api/v1/infrastructure/storage/buckets",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert response.status_code == 403
