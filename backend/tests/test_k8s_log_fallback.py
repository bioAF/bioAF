"""Tests that _k8s_get_job_logs falls back to pod status when kubelet is gone.

When a GKE node scales down, the kubelet becomes unavailable and
read_namespaced_pod_log raises an ApiException. The adapter should
fall back to reading the container's termination reason, exit code,
and message from the pod status object (which the API server retains).
"""

from unittest.mock import MagicMock, patch

import pytest
from kubernetes.client.rest import ApiException

from app.adapters.compute.kubernetes import KubernetesComputeProvider


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "k8s")
    provider = KubernetesComputeProvider()
    provider._namespace_ready = True
    return provider


def _make_pod(exit_code=1, reason="Error", message="", phase="Failed"):
    """Build a mock pod object with container status info."""
    pod = MagicMock()
    pod.metadata.name = "bioaf-pipeline-1-abc12"
    pod.status.phase = phase

    container_status = MagicMock()
    container_status.name = "pipeline"
    terminated = MagicMock()
    terminated.exit_code = exit_code
    terminated.reason = reason
    terminated.message = message
    container_status.state.terminated = terminated
    container_status.state.waiting = None

    pod.status.container_statuses = [container_status]
    pod.status.init_container_statuses = []
    return pod


class TestLogFallback:
    @pytest.mark.asyncio
    async def test_falls_back_to_pod_status_when_kubelet_unavailable(self, adapter):
        """When read_namespaced_pod_log fails, extract info from pod status."""
        pod = _make_pod(exit_code=1, reason="Error", message="")
        pod_list = MagicMock()
        pod_list.items = [pod]

        mock_core = MagicMock()
        mock_core.list_namespaced_pod.return_value = pod_list
        mock_core.read_namespaced_pod_log.side_effect = ApiException(status=500, reason="No agent available")

        with patch.object(adapter, "_get_k8s_core_client", return_value=mock_core):
            logs = await adapter._k8s_get_job_logs("bioaf-pipeline-1")

        # Should contain useful info, not "could not retrieve logs"
        assert "exit code" in logs.lower() or "exit_code" in logs.lower()
        assert "1" in logs

    @pytest.mark.asyncio
    async def test_includes_termination_message_when_available(self, adapter):
        """If the container has a termination message, include it."""
        pod = _make_pod(exit_code=137, reason="OOMKilled", message="memory limit exceeded")
        pod_list = MagicMock()
        pod_list.items = [pod]

        mock_core = MagicMock()
        mock_core.list_namespaced_pod.return_value = pod_list
        mock_core.read_namespaced_pod_log.side_effect = ApiException(status=500, reason="No agent available")

        with patch.object(adapter, "_get_k8s_core_client", return_value=mock_core):
            logs = await adapter._k8s_get_job_logs("bioaf-pipeline-1")

        assert "OOMKilled" in logs
        assert "137" in logs

    @pytest.mark.asyncio
    async def test_returns_normal_logs_when_kubelet_available(self, adapter):
        """Happy path: kubelet is up, return actual container logs."""
        pod = _make_pod()
        pod_list = MagicMock()
        pod_list.items = [pod]

        mock_core = MagicMock()
        mock_core.list_namespaced_pod.return_value = pod_list
        mock_core.read_namespaced_pod_log.return_value = "Nextflow output here..."

        with patch.object(adapter, "_get_k8s_core_client", return_value=mock_core):
            logs = await adapter._k8s_get_job_logs("bioaf-pipeline-1")

        assert logs == "Nextflow output here..."
