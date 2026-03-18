"""Tests for out-of-cluster GKE authentication.

When the backend runs outside the GKE cluster (e.g., Docker Compose on a VM),
the K8s adapter should build a client from platform_config credentials
(gke_cluster_endpoint, gke_cluster_ca_cert, GCP service account key).
"""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.adapters.compute.kubernetes import KubernetesComputeProvider


@pytest.fixture
def adapter_k8s_mode(monkeypatch):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "k8s")
    provider = KubernetesComputeProvider()
    provider._namespace_ready = True
    return provider


@pytest.fixture
def platform_config():
    """Simulated platform_config values stored after stack deploy."""
    return {
        "gke_cluster_endpoint": "https://10.0.0.1",
        "gke_cluster_ca_cert": base64.b64encode(b"test-ca-cert").decode(),
        "gcp_credential_source": "service_account_key",
        "gcp_service_account_key": '{"type": "service_account", "project_id": "test"}',
    }


class TestOutOfClusterAuth:
    @pytest.mark.asyncio
    async def test_falls_back_to_platform_config_when_not_in_cluster(self, adapter_k8s_mode, platform_config):
        """When load_incluster_config fails, adapter uses platform_config creds."""
        mock_batch = MagicMock()
        mock_job = MagicMock()
        mock_job.metadata.name = "bioaf-pipeline-1"
        mock_batch.create_namespaced_job.return_value = mock_job

        # Simulate not being inside a cluster
        with patch(
            "app.adapters.compute.kubernetes.config.load_incluster_config",
            side_effect=Exception("not in cluster"),
        ):
            with patch(
                "app.adapters.compute.kubernetes.KubernetesComputeProvider._build_out_of_cluster_client",
                return_value=MagicMock(),
            ) as mock_build:
                adapter_k8s_mode._get_k8s_batch_client()
                mock_build.assert_called_once()

    @pytest.mark.asyncio
    async def test_prefers_incluster_config_when_available(self, adapter_k8s_mode):
        """When running inside a pod, uses incluster config (no fallback)."""
        with patch("app.adapters.compute.kubernetes.config.load_incluster_config") as mock_incluster:
            with patch(
                "app.adapters.compute.kubernetes.KubernetesComputeProvider._build_out_of_cluster_client",
            ) as mock_build:
                adapter_k8s_mode._get_k8s_batch_client()
                mock_incluster.assert_called_once()
                mock_build.assert_not_called()

    @pytest.mark.asyncio
    async def test_out_of_cluster_client_uses_endpoint_and_ca(self, adapter_k8s_mode, platform_config):
        """_build_out_of_cluster_client configures ApiClient with endpoint + CA."""
        adapter_k8s_mode._cluster_config = platform_config

        with patch(
            "app.adapters.compute.kubernetes.config.load_incluster_config",
            side_effect=Exception("not in cluster"),
        ):
            with patch("app.adapters.compute.kubernetes.client.Configuration") as mock_config_cls:
                mock_config = MagicMock()
                mock_config_cls.return_value = mock_config

                with patch("app.adapters.compute.kubernetes.client.ApiClient"):
                    with patch(
                        "app.adapters.compute.kubernetes._get_gcp_token",
                        return_value="fake-token",
                    ):
                        adapter_k8s_mode._build_out_of_cluster_client()

                mock_config_cls.assert_called_once()
                assert mock_config.host == "https://10.0.0.1"
                assert mock_config.api_key == {"authorization": "Bearer fake-token"}

    @pytest.mark.asyncio
    async def test_submit_job_works_with_out_of_cluster_auth(self, adapter_k8s_mode, platform_config):
        """Full submit_job flow using out-of-cluster auth."""
        mock_batch = MagicMock()
        mock_job = MagicMock()
        mock_job.metadata.name = "bioaf-pipeline-99"
        mock_batch.create_namespaced_job.return_value = mock_job

        with patch.object(adapter_k8s_mode, "_get_k8s_batch_client", return_value=mock_batch):
            result = await adapter_k8s_mode._k8s_submit_job(
                {
                    "run_id": 99,
                    "pipeline_name": "test",
                    "container_image": "alpine:3.19",
                    "command": ["echo"],
                    "namespace": "bioaf-pipelines",
                    "input_files": [],
                    "parameters": {},
                }
            )

        assert result["job_id"] == "bioaf-pipeline-99"
        mock_batch.create_namespaced_job.assert_called_once()

    @pytest.mark.asyncio
    async def test_load_cluster_config_is_async(self, adapter_k8s_mode):
        """load_cluster_config must be async to avoid event loop conflicts with asyncpg."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            ("gke_cluster_endpoint", "https://10.0.0.1"),
            ("gke_cluster_ca_cert", "dGVzdA=="),
        ]
        mock_session.execute.return_value = mock_result

        # session_factory() returns an async context manager (like AsyncSession)
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_session
        mock_ctx.__aexit__.return_value = False

        adapter_k8s_mode._session_factory = MagicMock(return_value=mock_ctx)

        cfg = await adapter_k8s_mode.load_cluster_config()
        assert cfg["gke_cluster_endpoint"] == "https://10.0.0.1"
        # Should cache the result
        assert adapter_k8s_mode._cluster_config is not None
