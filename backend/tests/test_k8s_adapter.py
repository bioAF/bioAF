"""Tests for the Kubernetes compute adapter production mode (Phase 19).

Verifies get_cluster_status and get_cluster_metrics return data using mocked GKE client.
"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_k8s_adapter_get_cluster_status_with_mock():
    """Adapter get_cluster_status returns pool info from mocked GKE API."""
    from app.adapters.compute.kubernetes import KubernetesComputeProvider

    provider = KubernetesComputeProvider()
    provider._mode = "production"
    # Seed cluster identity so _k8s_get_cluster_status passes its sanity check.
    # Also seed an endpoint so load_cluster_config considers the cache valid
    # (otherwise it overwrites with {} when no session_factory is configured).
    provider._cluster_config = {
        "gke_cluster_endpoint": "https://10.0.0.1",
        "gke_cluster_name": "bioaf-test",
        "gcp_project_id": "test-project",
        "gcp_region": "us-central1",
    }

    # Mock GKE cluster response
    mock_cluster = MagicMock()
    mock_cluster.name = "bioaf-test"
    mock_cluster.status = 2  # RUNNING
    mock_cluster.current_node_count = 0

    mock_pipeline_pool = MagicMock()
    mock_pipeline_pool.name = "bioaf-pipelines"
    mock_pipeline_pool.config.machine_type = "n2-highmem-8"
    mock_pipeline_pool.autoscaling.min_node_count = 0
    mock_pipeline_pool.autoscaling.max_node_count = 20
    mock_pipeline_pool.initial_node_count = 0
    mock_pipeline_pool.config.spot = True
    mock_pipeline_pool.status = 2

    mock_interactive_pool = MagicMock()
    mock_interactive_pool.name = "bioaf-interactive"
    mock_interactive_pool.config.machine_type = "n2-standard-4"
    mock_interactive_pool.autoscaling.min_node_count = 0
    mock_interactive_pool.autoscaling.max_node_count = 5
    mock_interactive_pool.initial_node_count = 0
    mock_interactive_pool.config.spot = False
    mock_interactive_pool.status = 2

    mock_cluster.node_pools = [mock_pipeline_pool, mock_interactive_pool]

    mock_client = MagicMock()
    mock_client.get_cluster.return_value = mock_cluster

    with patch(
        "app.adapters.compute.kubernetes.KubernetesComputeProvider._get_gke_client",
        return_value=mock_client,
    ):
        status = await provider.get_cluster_status()

    assert status["controller_status"] == "running"
    assert status["health"] == "healthy"
    assert len(status["node_pools"]) == 2

    pipeline = next(p for p in status["node_pools"] if p["name"] == "bioaf-pipelines")
    assert pipeline["machine_type"] == "n2-highmem-8"
    assert pipeline["max_nodes"] == 20
    assert pipeline["spot"] is True

    interactive = next(p for p in status["node_pools"] if p["name"] == "bioaf-interactive")
    assert interactive["machine_type"] == "n2-standard-4"
    assert interactive["max_nodes"] == 5
    assert interactive["spot"] is False


@pytest.mark.asyncio
async def test_k8s_adapter_get_cluster_metrics_with_mock():
    """Adapter get_cluster_metrics returns metrics from mocked GKE API."""
    from app.adapters.compute.kubernetes import KubernetesComputeProvider

    provider = KubernetesComputeProvider()
    provider._mode = "production"

    mock_cluster = MagicMock()
    mock_cluster.name = "bioaf-test"
    mock_cluster.status = 2
    mock_cluster.current_node_count = 1

    mock_pool = MagicMock()
    mock_pool.name = "bioaf-pipelines"
    mock_pool.config.machine_type = "n2-highmem-8"
    mock_pool.autoscaling.min_node_count = 0
    mock_pool.autoscaling.max_node_count = 20
    mock_pool.initial_node_count = 0
    mock_pool.status = 2

    mock_cluster.node_pools = [mock_pool]

    mock_client = MagicMock()
    mock_client.get_cluster.return_value = mock_cluster

    with patch(
        "app.adapters.compute.kubernetes.KubernetesComputeProvider._get_gke_client",
        return_value=mock_client,
    ):
        metrics = await provider.get_cluster_metrics()

    assert "cpu_utilization_pct" in metrics
    assert "memory_utilization_pct" in metrics
    assert "cost_burn_rate_hourly" in metrics
    assert "node_pools" in metrics
