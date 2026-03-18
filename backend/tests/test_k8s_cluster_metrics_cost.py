"""Tests that _k8s_get_cluster_metrics returns non-zero cost rates.

The billing widget depends on cost_rate_hourly from cluster metrics
to calculate daily spend. When all rates are 0.0, the cost widget
shows no data.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.adapters.compute.kubernetes import KubernetesComputeProvider


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "k8s")
    provider = KubernetesComputeProvider()
    provider._namespace_ready = True
    return provider


def _mock_gke_cluster():
    """Build a mock GKE cluster response with realistic node pools."""
    mock_cluster = MagicMock()
    mock_cluster.status = 2  # RUNNING

    pool_platform = MagicMock()
    pool_platform.name = "bioaf-platform"
    pool_platform.config.machine_type = "e2-standard-2"
    pool_platform.config.spot = False
    pool_platform.initial_node_count = 1
    pool_platform.autoscaling.min_node_count = 1
    pool_platform.autoscaling.max_node_count = 3

    pool_pipelines = MagicMock()
    pool_pipelines.name = "bioaf-pipelines"
    pool_pipelines.config.machine_type = "n2-highmem-8"
    pool_pipelines.config.spot = True
    pool_pipelines.initial_node_count = 2
    pool_pipelines.autoscaling.min_node_count = 0
    pool_pipelines.autoscaling.max_node_count = 20

    pool_interactive = MagicMock()
    pool_interactive.name = "bioaf-interactive"
    pool_interactive.config.machine_type = "n2-standard-4"
    pool_interactive.config.spot = False
    pool_interactive.initial_node_count = 0
    pool_interactive.autoscaling.min_node_count = 0
    pool_interactive.autoscaling.max_node_count = 5

    mock_cluster.node_pools = [pool_platform, pool_pipelines, pool_interactive]
    return mock_cluster


class TestClusterMetricsCostRates:
    @pytest.mark.asyncio
    async def test_cost_rate_nonzero_for_active_nodes(self, adapter, monkeypatch):
        """Active node pools must report non-zero cost_rate_hourly."""
        monkeypatch.setenv("GKE_CLUSTER_NAME", "test-cluster")
        monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
        monkeypatch.setenv("GCP_ZONE", "us-central1")

        mock_cluster = _mock_gke_cluster()
        mock_gke = MagicMock()
        mock_gke.get_cluster.return_value = mock_cluster

        with patch.object(adapter, "_get_gke_client", return_value=mock_gke):
            result = await adapter._k8s_get_cluster_metrics()

        # Platform pool has 1 active node -- cost should be > 0
        platform_pool = next(p for p in result["node_pools"] if p["name"] == "bioaf-platform")
        assert platform_pool["cost_rate_hourly"] > 0, "Platform pool with active nodes should have non-zero cost"

        # Pipelines pool has 2 active nodes -- cost should be > 0
        pipelines_pool = next(p for p in result["node_pools"] if p["name"] == "bioaf-pipelines")
        assert pipelines_pool["cost_rate_hourly"] > 0, "Pipelines pool with active nodes should have non-zero cost"

        # Interactive pool has 0 nodes -- cost should be 0
        interactive_pool = next(p for p in result["node_pools"] if p["name"] == "bioaf-interactive")
        assert interactive_pool["cost_rate_hourly"] == 0.0, "Pool with 0 nodes should have zero cost"

    @pytest.mark.asyncio
    async def test_total_burn_rate_sums_pools(self, adapter, monkeypatch):
        """Total cost_burn_rate_hourly should equal sum of pool rates."""
        monkeypatch.setenv("GKE_CLUSTER_NAME", "test-cluster")
        monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
        monkeypatch.setenv("GCP_ZONE", "us-central1")

        mock_cluster = _mock_gke_cluster()
        mock_gke = MagicMock()
        mock_gke.get_cluster.return_value = mock_cluster

        with patch.object(adapter, "_get_gke_client", return_value=mock_gke):
            result = await adapter._k8s_get_cluster_metrics()

        pool_total = sum(p["cost_rate_hourly"] for p in result["node_pools"])
        assert result["cost_burn_rate_hourly"] == pytest.approx(pool_total, rel=0.01)
        assert result["cost_burn_rate_hourly"] > 0

    @pytest.mark.asyncio
    async def test_spot_pricing_applied(self, adapter, monkeypatch):
        """Spot pools should have lower cost than on-demand for the same machine type."""
        monkeypatch.setenv("GKE_CLUSTER_NAME", "test-cluster")
        monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
        monkeypatch.setenv("GCP_ZONE", "us-central1")

        mock_cluster = _mock_gke_cluster()
        mock_gke = MagicMock()
        mock_gke.get_cluster.return_value = mock_cluster

        with patch.object(adapter, "_get_gke_client", return_value=mock_gke):
            result = await adapter._k8s_get_cluster_metrics()

        pipelines_pool = next(p for p in result["node_pools"] if p["name"] == "bioaf-pipelines")
        # n2-highmem-8 spot with 2 nodes should cost less per-node than on-demand
        per_node_rate = pipelines_pool["cost_rate_hourly"] / 2
        # Spot is typically ~60-70% discount, so per-node should be < $0.30
        # (n2-highmem-8 on-demand is ~$0.52/hr)
        assert per_node_rate < 0.40, f"Spot per-node rate {per_node_rate} seems too high"
