"""Tests for the infrastructure status API endpoints."""

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.adapters import registry


@pytest.fixture(autouse=True)
def init_adapters(monkeypatch):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "local")
    registry.reset_registry()
    registry.initialize_adapters_sync("kubernetes")
    yield
    registry.reset_registry()


@pytest_asyncio.fixture
async def bench_user(session, admin_user):
    from app.models.user import User
    from app.services.auth_service import AuthService

    user = User(
        email="bench@test.com",
        password_hash=AuthService.hash_password("benchpass123"),
        role_id=admin_user._test_role_map["bench"],
        organization_id=admin_user.organization_id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return user


@pytest_asyncio.fixture
async def bench_token(bench_user):
    from app.services.auth_service import AuthService

    return AuthService.create_token(
        bench_user.id, bench_user.email, bench_user.role_id, bench_user.organization_id, role_name="bench"
    )


class TestStorageMetricsEndpoint:
    @pytest.mark.asyncio
    async def test_returns_storage_metrics(self, client, admin_token):
        response = await client.get(
            "/api/v1/infrastructure/storage/metrics",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "buckets" in data
        assert "total_size_gb" in data
        assert "total_cost_monthly_usd" in data
        assert len(data["buckets"]) == 5

    @pytest.mark.asyncio
    async def test_bench_denied(self, client, bench_token):
        response = await client.get(
            "/api/v1/infrastructure/storage/metrics",
            headers={"Authorization": f"Bearer {bench_token}"},
        )
        assert response.status_code == 403


class TestComputeStackEndpoint:
    @pytest.mark.asyncio
    async def test_returns_compute_stack(self, client, admin_token, session):
        # Insert the platform_config row
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES ('compute_stack', 'kubernetes') ON CONFLICT (key) DO NOTHING"
            )
        )
        await session.commit()

        response = await client.get(
            "/api/v1/infrastructure/compute/stack",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["compute_stack"] == "kubernetes"

    @pytest.mark.asyncio
    async def test_defaults_to_kubernetes(self, client, admin_token):
        response = await client.get(
            "/api/v1/infrastructure/compute/stack",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["compute_stack"] == "kubernetes"


class TestStackOptionsEndpoint:
    @pytest.mark.asyncio
    async def test_defaults_to_gcp_options(self, client, admin_token):
        """No cloud_provider row -> GCP options (behavior-preserving)."""
        response = await client.get(
            "/api/v1/infrastructure/stack-options",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["cloud_provider"] == "gcp"
        labels = {o["label"] for o in data["options"]}
        assert labels == {"Kubernetes + GCS", "SLURM + NFS"}
        k8s = next(o for o in data["options"] if o["compute_stack"] == "kubernetes")
        assert k8s["compute_label"] == "Kubernetes (GKE)"
        assert k8s["available"] is True and k8s["recommended"] is True

    @pytest.mark.asyncio
    async def test_aws_cloud_provider_yields_eks_s3(self, client, admin_token, session):
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES ('cloud_provider', 'aws') "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            )
        )
        await session.commit()

        response = await client.get(
            "/api/v1/infrastructure/stack-options",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["cloud_provider"] == "aws"
        k8s = next(o for o in data["options"] if o["compute_stack"] == "kubernetes")
        assert k8s["label"] == "Kubernetes + S3"
        assert k8s["compute_label"] == "Kubernetes (EKS)"
        assert k8s["storage_backend"] == "s3"

    @pytest.mark.asyncio
    async def test_bench_denied(self, client, bench_token):
        response = await client.get(
            "/api/v1/infrastructure/stack-options",
            headers={"Authorization": f"Bearer {bench_token}"},
        )
        assert response.status_code == 403
