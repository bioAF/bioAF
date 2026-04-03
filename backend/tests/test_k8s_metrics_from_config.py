"""Tests that _k8s_get_cluster_metrics reads cluster identity from platform_config.

The GKE cluster name, project ID, and region are stored in platform_config
(written during stack deployment). The metrics method should read them from
_cluster_config rather than requiring separate environment variables.
Also, if the GKE API call fails, get_cluster_metrics must return a safe
fallback so the /api/costs/summary endpoint does not 500.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.adapters.compute.kubernetes import KubernetesComputeProvider


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "k8s")
    provider = KubernetesComputeProvider()
    provider._namespace_ready = True
    provider._cluster_config = {
        "gke_cluster_endpoint": "https://10.0.0.1",
        "gke_cluster_name": "bioaf-cluster-1",
        "gcp_project_id": "my-project",
        "gcp_region": "us-central1",
        "gcp_service_account_key": '{"type": "service_account", "project_id": "test"}',
    }
    return provider


class TestMetricsFromConfig:
    @pytest.mark.asyncio
    async def test_reads_cluster_identity_from_platform_config(self, adapter):
        """_k8s_get_cluster_metrics should use _cluster_config, not env vars."""
        mock_cluster = MagicMock()
        mock_cluster.status = 2  # RUNNING
        pool = MagicMock()
        pool.name = "bioaf-platform"
        pool.config.machine_type = "e2-standard-2"
        pool.config.spot = False
        pool.initial_node_count = 1
        mock_cluster.node_pools = [pool]

        mock_gke = MagicMock()
        mock_gke.get_cluster.return_value = mock_cluster

        with patch.object(adapter, "_get_gke_client", return_value=mock_gke):
            result = await adapter._k8s_get_cluster_metrics()

        # Verify the GKE client was called with the config values, not env vars
        expected_name = "projects/my-project/locations/us-central1/clusters/bioaf-cluster-1"
        mock_gke.get_cluster.assert_called_once_with(name=expected_name)
        assert result["cost_burn_rate_hourly"] > 0

    @pytest.mark.asyncio
    async def test_metrics_returns_safe_fallback_on_gke_error(self, adapter):
        """If the GKE API call fails, return zeros instead of crashing."""
        mock_gke = MagicMock()
        mock_gke.get_cluster.side_effect = Exception("403 Forbidden")

        with patch.object(adapter, "_get_gke_client", return_value=mock_gke):
            result = await adapter._k8s_get_cluster_metrics()

        assert result["cost_burn_rate_hourly"] == 0.0
        assert result["node_pools"] == []

    @pytest.mark.asyncio
    async def test_metrics_works_without_env_vars(self, adapter, monkeypatch):
        """Even without GKE_CLUSTER_NAME env var, metrics should work from config."""
        monkeypatch.delenv("GKE_CLUSTER_NAME", raising=False)
        monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
        monkeypatch.delenv("GCP_ZONE", raising=False)

        mock_cluster = MagicMock()
        mock_cluster.status = 2
        pool = MagicMock()
        pool.name = "bioaf-platform"
        pool.config.machine_type = "e2-standard-2"
        pool.config.spot = False
        pool.initial_node_count = 1
        mock_cluster.node_pools = [pool]

        mock_gke = MagicMock()
        mock_gke.get_cluster.return_value = mock_cluster

        with patch.object(adapter, "_get_gke_client", return_value=mock_gke):
            result = await adapter._k8s_get_cluster_metrics()

        assert result["cost_burn_rate_hourly"] > 0
