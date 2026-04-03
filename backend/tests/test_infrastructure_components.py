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
    async def test_returns_k8s_components_when_kubernetes_stack(self, client, admin_token, session):
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES ('compute_stack', 'kubernetes') "
                "ON CONFLICT (key) DO UPDATE SET value = 'kubernetes'"
            )
        )
        await session.commit()

        response = await client.get(
            "/api/v1/infrastructure/components",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "components" in data
        assert "compute_stack" in data
        assert data["compute_stack"] == "kubernetes"

        keys = [c["key"] for c in data["components"]]
        assert "k8s_pipeline_pool" in keys
        assert "k8s_interactive_pool" in keys

    @pytest.mark.asyncio
    async def test_slurm_listed_as_coming_soon_on_kubernetes(self, client, admin_token, session):
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES ('compute_stack', 'kubernetes') "
                "ON CONFLICT (key) DO UPDATE SET value = 'kubernetes'"
            )
        )
        await session.commit()

        response = await client.get(
            "/api/v1/infrastructure/components",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()

        slurm = next((c for c in data["components"] if c["key"] == "slurm"), None)
        assert slurm is not None
        assert slurm["status"] == "coming_soon"

        filestore = next((c for c in data["components"] if c["key"] == "filestore"), None)
        assert filestore is not None
        assert filestore["status"] == "coming_soon"

    @pytest.mark.asyncio
    async def test_snakemake_listed_as_coming_soon_on_kubernetes(self, client, admin_token, session):
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES ('compute_stack', 'kubernetes') "
                "ON CONFLICT (key) DO UPDATE SET value = 'kubernetes'"
            )
        )
        await session.commit()

        response = await client.get(
            "/api/v1/infrastructure/components",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()

        snakemake_k8s = next((c for c in data["components"] if c["key"] == "snakemake_k8s"), None)
        assert snakemake_k8s is not None
        assert snakemake_k8s["status"] == "coming_soon"

        snakemake_slurm = next((c for c in data["components"] if c["key"] == "snakemake"), None)
        assert snakemake_slurm is not None
        assert snakemake_slurm["status"] == "coming_soon"

    @pytest.mark.asyncio
    async def test_nextflow_k8s_available_on_kubernetes(self, client, admin_token, session):
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES ('compute_stack', 'kubernetes') "
                "ON CONFLICT (key) DO UPDATE SET value = 'kubernetes'"
            )
        )
        await session.commit()

        response = await client.get(
            "/api/v1/infrastructure/components",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()

        nextflow_k8s = next((c for c in data["components"] if c["key"] == "nextflow_k8s"), None)
        assert nextflow_k8s is not None
        assert nextflow_k8s["status"] == "available"

    @pytest.mark.asyncio
    async def test_response_includes_all_expected_categories(self, client, admin_token, session):
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES ('compute_stack', 'kubernetes') "
                "ON CONFLICT (key) DO UPDATE SET value = 'kubernetes'"
            )
        )
        await session.commit()

        response = await client.get(
            "/api/v1/infrastructure/components",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()

        categories = {c["category"] for c in data["components"]}
        assert "compute" in categories
        assert "pipeline_orchestration" in categories
        assert "analysis" in categories
        assert "visualization" in categories
        assert "search" in categories

    @pytest.mark.asyncio
    async def test_requires_admin_or_comp_bio_role(self, client, viewer_token):
        response = await client.get(
            "/api/v1/infrastructure/components",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_defaults_to_kubernetes_when_not_configured(self, client, admin_token):
        response = await client.get(
            "/api/v1/infrastructure/components",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["compute_stack"] == "kubernetes"
        keys = [c["key"] for c in data["components"]]
        assert "k8s_pipeline_pool" in keys


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
    async def test_returns_five_buckets_when_deployed(self, client, admin_token, session):
        for key, value in [
            ("storage_deployed", "true"),
            ("ingest_bucket_name", "bioaf-ingest-demo"),
            ("raw_bucket_name", "bioaf-raw-demo"),
            ("working_bucket_name", "bioaf-working-demo"),
            ("results_bucket_name", "bioaf-results-demo"),
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
            for p in ["ingest", "raw", "working", "results", "config_backups"]
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
        assert len(data["buckets"]) == 5

    @pytest.mark.asyncio
    async def test_each_bucket_has_required_fields(self, client, admin_token, session):
        for key, value in [
            ("storage_deployed", "true"),
            ("ingest_bucket_name", "bioaf-ingest-demo"),
            ("raw_bucket_name", "bioaf-raw-demo"),
            ("working_bucket_name", "bioaf-working-demo"),
            ("results_bucket_name", "bioaf-results-demo"),
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
            for p in ["ingest", "raw", "working", "results", "config_backups"]
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
