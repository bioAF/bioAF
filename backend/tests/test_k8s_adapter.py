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

    assert status.controller_status == "running"
    assert status.health == "healthy"
    assert len(status.node_pools) == 2

    pipeline = next(p for p in status.node_pools if p.name == "bioaf-pipelines")
    assert pipeline.machine_type == "n2-highmem-8"
    assert pipeline.max_nodes == 20
    assert pipeline.spot is True

    interactive = next(p for p in status.node_pools if p.name == "bioaf-interactive")
    assert interactive.machine_type == "n2-standard-4"
    assert interactive.max_nodes == 5
    assert interactive.spot is False


@pytest.mark.asyncio
async def test_k8s_adapter_get_cluster_detail_with_mock():
    """get_cluster_detail returns the stack-view ClusterDetail (name/status/
    node-count + per-pool detail), with status mapped to the uppercase enum-name
    contract the stack view expects (distinct from get_cluster_status's lowercase
    controller_status)."""
    from app.adapters.compute.kubernetes import KubernetesComputeProvider

    provider = KubernetesComputeProvider()
    provider._mode = "production"
    provider._cluster_config = {
        "gke_cluster_endpoint": "https://10.0.0.1",
        "gke_cluster_name": "bioaf-test",
        "gcp_project_id": "test-project",
        "gcp_region": "us-central1",
    }

    mock_cluster = MagicMock()
    mock_cluster.name = "bioaf-test"
    mock_cluster.status = 2  # RUNNING
    mock_cluster.current_node_count = 3

    mock_pool = MagicMock()
    mock_pool.name = "bioaf-pipelines"
    mock_pool.config.machine_type = "n2-highmem-8"
    mock_pool.autoscaling.min_node_count = 0
    mock_pool.autoscaling.max_node_count = 20
    mock_pool.initial_node_count = 3
    mock_pool.config.spot = True
    mock_pool.status = 99  # unmapped -> UNKNOWN

    mock_cluster.node_pools = [mock_pool]

    mock_client = MagicMock()
    mock_client.get_cluster.return_value = mock_cluster

    with patch(
        "app.adapters.compute.kubernetes.KubernetesComputeProvider._get_gke_client",
        return_value=mock_client,
    ):
        detail = await provider.get_cluster_detail()

    assert detail.name == "bioaf-test"
    assert detail.status == "RUNNING"
    assert detail.node_count == 3
    assert len(detail.node_pools) == 1
    pool = detail.node_pools[0]
    assert pool.name == "bioaf-pipelines"
    assert pool.machine_type == "n2-highmem-8"
    assert pool.max_nodes == 20
    assert pool.spot is True
    assert pool.status == "UNKNOWN"  # unmapped enum falls back to UNKNOWN


@pytest.mark.asyncio
async def test_k8s_adapter_cluster_lifecycle_management():
    """list_cluster_names / probe_cluster / delete_cluster own the orphan
    scan/recovery/teardown GKE calls drained from orphaned_resource (Stage 3b.5)."""
    from app.adapters.compute.kubernetes import KubernetesComputeProvider

    provider = KubernetesComputeProvider()
    provider._mode = "production"

    c1 = MagicMock()
    c1.name = "bioaf-a"
    c2 = MagicMock()
    c2.name = "bioaf-b"

    probe_cluster = MagicMock()
    probe_cluster.status = 2  # RUNNING
    probe_cluster.endpoint = "10.0.0.1"
    probe_cluster.master_auth.cluster_ca_certificate = "fake-ca"

    mock_client = MagicMock()
    mock_client.list_clusters.return_value.clusters = [c1, c2]
    mock_client.get_cluster.return_value = probe_cluster

    with patch(
        "app.adapters.compute.kubernetes.KubernetesComputeProvider._get_gke_client",
        return_value=mock_client,
    ):
        names = await provider.list_cluster_names("proj", "us-central1-a")
        probe = await provider.probe_cluster("proj", "us-central1-a", "bioaf-a")
        await provider.delete_cluster("proj", "us-central1-a", "bioaf-a")

    assert names == ["bioaf-a", "bioaf-b"]
    mock_client.list_clusters.assert_called_once_with(parent="projects/proj/locations/us-central1-a")
    assert probe.state == "RUNNING"
    assert probe.endpoint == "10.0.0.1"
    assert probe.ca_cert == "fake-ca"
    mock_client.delete_cluster.assert_called_once_with(name="projects/proj/locations/us-central1-a/clusters/bioaf-a")


@pytest.mark.asyncio
async def test_k8s_adapter_probe_cluster_not_found():
    """A cluster that can't be fetched probes as NOT_FOUND (orphan already gone)."""
    from app.adapters.compute.kubernetes import KubernetesComputeProvider

    provider = KubernetesComputeProvider()
    provider._mode = "production"

    mock_client = MagicMock()
    mock_client.get_cluster.side_effect = Exception("404 not found")

    with patch(
        "app.adapters.compute.kubernetes.KubernetesComputeProvider._get_gke_client",
        return_value=mock_client,
    ):
        probe = await provider.probe_cluster("proj", "zone-a", "gone")

    assert probe.state == "NOT_FOUND"
    assert probe.endpoint is None


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

    assert metrics.cpu_utilization_pct is not None
    assert metrics.memory_utilization_pct is not None
    assert metrics.cost_burn_rate_hourly is not None
    assert metrics.node_pools is not None
