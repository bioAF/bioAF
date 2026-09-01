"""Tests for the cluster-config quota preflight at the API boundary.

An operator moved the pipeline pool to pd-balanced at 500 GB. The API accepted
it, terraform applied it, GKE reported the pool RUNNING, and the pool could not
create a single node because pd-balanced bills to SSD_TOTAL_GB whose regional
limit was 500 GB. These tests pin the boundary refusing that, and pin the two
things it must NOT refuse: a config that merely runs at reduced concurrency, and
a config whose quota could not be read.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.adapters.models import QuotaMetric
from tests.test_stack_api import _set_config


def _quotas(**overrides):
    """The quotas bioaf-495400/us-central1 reported during the incident."""
    quotas = {
        "SSD_TOTAL_GB": QuotaMetric(metric="SSD_TOTAL_GB", usage=30.0, limit=500.0),
        "DISKS_TOTAL_GB": QuotaMetric(metric="DISKS_TOTAL_GB", usage=90.0, limit=4096.0),
        "CPUS": QuotaMetric(metric="CPUS", usage=7.0, limit=200.0),
        "N2_CPUS": QuotaMetric(metric="N2_CPUS", usage=0.0, limit=200.0),
        "PREEMPTIBLE_CPUS": QuotaMetric(metric="PREEMPTIBLE_CPUS", usage=0.0, limit=0.0),
    }
    quotas.update(overrides)
    return quotas


def _adapter(quotas=None, node_count=0):
    adapter = MagicMock()
    adapter.get_regional_quotas = AsyncMock(return_value=quotas)
    adapter.count_pool_nodes = AsyncMock(return_value=node_count)
    return adapter


async def _seed_pipeline_pool(session):
    await _set_config(session, "compute_deployed", "true")
    await _set_config(session, "compute_stack", "kubernetes")
    await _set_config(session, "k8s_pipeline_machine_type", "n2-highmem-16")
    await _set_config(session, "k8s_pipeline_max_nodes", "20")
    await _set_config(session, "k8s_pipeline_use_spot", "true")
    await _set_config(session, "k8s_pipeline_disk_size_gb", "100")
    await _set_config(session, "k8s_pipeline_disk_type", "pd-standard")
    await session.commit()


class TestPreflightEndpoint:
    @pytest.mark.asyncio
    async def test_preflight_blocks_the_config_that_caused_the_incident(self, client, admin_token, session):
        await _seed_pipeline_pool(session)

        with patch(
            "app.services.cluster_quota_service.get_compute_adapter",
            return_value=_adapter(_quotas()),
        ):
            response = await client.post(
                "/api/v1/infrastructure/cluster/config/preflight",
                json={"k8s_pipeline_disk_size_gb": 500, "k8s_pipeline_disk_type": "pd-balanced"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert response.status_code == 200
        verdict = response.json()
        assert verdict["status"] == "block"
        assert verdict["binding_metric"] == "SSD_TOTAL_GB"
        assert verdict["achievable_nodes"] == 0

    @pytest.mark.asyncio
    async def test_preflight_warns_on_reduced_concurrency(self, client, admin_token, session):
        """pd-standard at 500 GB x 20 fits 8 nodes. It runs; it is not refused."""
        await _seed_pipeline_pool(session)

        with patch(
            "app.services.cluster_quota_service.get_compute_adapter",
            return_value=_adapter(_quotas()),
        ):
            response = await client.post(
                "/api/v1/infrastructure/cluster/config/preflight",
                json={"k8s_pipeline_disk_size_gb": 500, "k8s_pipeline_disk_type": "pd-standard"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        verdict = response.json()
        assert verdict["status"] == "warn"
        assert verdict["achievable_nodes"] == 8

    @pytest.mark.asyncio
    async def test_preflight_is_unverified_when_quota_cannot_be_read(self, client, admin_token, session):
        await _seed_pipeline_pool(session)

        with patch(
            "app.services.cluster_quota_service.get_compute_adapter",
            return_value=_adapter(None),
        ):
            response = await client.post(
                "/api/v1/infrastructure/cluster/config/preflight",
                json={"k8s_pipeline_disk_size_gb": 500, "k8s_pipeline_disk_type": "pd-balanced"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert response.json()["status"] == "unverified"


class TestUpdateIsGatedOnTheVerdict:
    @pytest.mark.asyncio
    async def test_update_refuses_a_config_that_cannot_build_one_node(self, client, admin_token, session):
        await _seed_pipeline_pool(session)

        with (
            patch(
                "app.services.cluster_quota_service.get_compute_adapter",
                return_value=_adapter(_quotas()),
            ),
            patch("app.api.stack_deploy.TerraformExecutor.run_plan", new_callable=AsyncMock) as mock_plan,
        ):
            response = await client.post(
                "/api/v1/infrastructure/cluster/config",
                json={"k8s_pipeline_disk_size_gb": 500, "k8s_pipeline_disk_type": "pd-balanced"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert response.status_code == 400
        assert "SSD_TOTAL_GB" in response.json()["detail"]
        # The whole point: nothing was applied.
        mock_plan.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_allows_reduced_concurrency(self, client, admin_token, session):
        """A pool that runs 8 of 20 nodes works. Refusing it would be wrong."""
        await _seed_pipeline_pool(session)

        mock_run = MagicMock()
        mock_run.id = 101
        mock_run.status = "awaiting_confirmation"

        with (
            patch(
                "app.services.cluster_quota_service.get_compute_adapter",
                return_value=_adapter(_quotas()),
            ),
            patch("app.api.stack_deploy.TerraformExecutor.run_plan", new_callable=AsyncMock) as mock_plan,
            patch("app.api.stack_deploy._run_apply_background", new_callable=AsyncMock),
        ):
            mock_plan.return_value = mock_run
            response = await client.post(
                "/api/v1/infrastructure/cluster/config",
                json={"k8s_pipeline_disk_size_gb": 500, "k8s_pipeline_disk_type": "pd-standard"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_update_proceeds_when_quota_cannot_be_read(self, client, admin_token, session):
        """Fail open: a missing IAM role must not make the page unusable."""
        await _seed_pipeline_pool(session)

        mock_run = MagicMock()
        mock_run.id = 102
        mock_run.status = "awaiting_confirmation"

        with (
            patch(
                "app.services.cluster_quota_service.get_compute_adapter",
                return_value=_adapter(None),
            ),
            patch("app.api.stack_deploy.TerraformExecutor.run_plan", new_callable=AsyncMock) as mock_plan,
            patch("app.api.stack_deploy._run_apply_background", new_callable=AsyncMock),
        ):
            mock_plan.return_value = mock_run
            response = await client.post(
                "/api/v1/infrastructure/cluster/config",
                json={"k8s_pipeline_disk_size_gb": 500, "k8s_pipeline_disk_type": "pd-balanced"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert response.status_code == 200


class TestPoolViabilityOnGet:
    @pytest.mark.asyncio
    async def test_get_config_reports_what_the_current_pool_can_build(self, client, admin_token, session):
        """Terraform reporting success and the pool reporting RUNNING were both
        true of a pool that could not create an instance. This is the field that
        distinguishes them, without waiting for a run to try."""
        await _seed_pipeline_pool(session)
        await _set_config(session, "k8s_pipeline_disk_size_gb", "500")
        await _set_config(session, "k8s_pipeline_disk_type", "pd-balanced")
        await session.commit()

        with patch(
            "app.services.cluster_quota_service.get_compute_adapter",
            return_value=_adapter(_quotas()),
        ):
            response = await client.get(
                "/api/v1/infrastructure/cluster/config",
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert response.status_code == 200
        quota = response.json()["pipeline_pool_quota"]
        assert quota["status"] == "block"
        assert quota["achievable_nodes"] == 0
        assert "SSD_TOTAL_GB" in quota["message"]
