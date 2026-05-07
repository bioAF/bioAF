"""Tests that the GKE client uses platform_config-derived credentials.

The GKE ClusterManager API requires proper auth. When running outside the
cluster, _get_gke_client must use credentials returned by credential_injector
(impersonated bootstrap on vm_default installs, JSON key in legacy mode),
not default/ambient VM credentials.
"""

import base64
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
        "gke_cluster_ca_cert": base64.b64encode(b"test-ca-cert").decode(),
        "gcp_credential_source": "service_account_key",
        "gcp_service_account_key": '{"type": "service_account", "project_id": "test"}',
    }
    return provider


class TestGkeClientAuth:
    def test_gke_client_uses_platform_config_credentials(self, adapter):
        """_get_gke_client should pass explicit credentials from platform_config."""
        mock_creds = MagicMock()

        with patch("google.cloud.container_v1.ClusterManagerClient") as mock_client_cls:
            with patch(
                "app.adapters.compute.kubernetes._load_gcp_credentials",
                return_value=mock_creds,
            ):
                adapter._get_gke_client()

            # Must pass credentials explicitly, not rely on ambient/default
            mock_client_cls.assert_called_once_with(credentials=mock_creds)

    def test_gke_client_falls_back_to_default_when_credential_load_fails(self, adapter):
        """When credential_injector raises (e.g. no GCP config), fall back to ADC."""
        adapter._cluster_config = {}

        with (
            patch("google.cloud.container_v1.ClusterManagerClient") as mock_client_cls,
            patch(
                "app.adapters.compute.kubernetes._load_gcp_credentials",
                side_effect=RuntimeError("no creds"),
            ),
        ):
            adapter._get_gke_client()
            # Called without explicit credentials
            mock_client_cls.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_cluster_metrics_uses_authenticated_gke_client(self, adapter, monkeypatch):
        """_k8s_get_cluster_metrics should work with authenticated GKE client."""
        monkeypatch.setenv("GKE_CLUSTER_NAME", "test-cluster")
        monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
        monkeypatch.setenv("GCP_ZONE", "us-central1")

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

        assert result["cost_burn_rate_hourly"] > 0
        mock_gke.get_cluster.assert_called_once()
