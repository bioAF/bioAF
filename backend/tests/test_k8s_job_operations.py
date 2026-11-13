"""Tests for K8s compute adapter job operations (spec tests 5-9).

Tests get_job_status, get_job_logs, and cancel_job with mocked K8s API.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.settings_contract import ComputeMode
from app.adapters.compute.kubernetes import KubernetesComputeProvider


@pytest.fixture
def adapter(mode_adapter_factory):
    provider = mode_adapter_factory(KubernetesComputeProvider, mode=ComputeMode.KUBERNETES)
    provider._namespace_ready = True
    return provider


class TestGetJobStatusRunning:
    @pytest.mark.asyncio
    async def test_running_status(self, adapter):
        """Test 5: get_job_status returns running when pods are active."""
        mock_batch = MagicMock()

        mock_job = MagicMock()
        mock_job.status.conditions = None
        mock_job.status.active = 1
        mock_job.status.succeeded = None
        mock_job.status.failed = None
        mock_batch.read_namespaced_job.return_value = mock_job

        # Mock pod listing
        mock_core = MagicMock()
        mock_pod = MagicMock()
        mock_pod.metadata.name = "bioaf-pipeline-42-xyz"
        mock_pod.spec.node_name = "gke-node-1"
        mock_pod_list = MagicMock()
        mock_pod_list.items = [mock_pod]
        mock_core.list_namespaced_pod.return_value = mock_pod_list

        with patch.object(adapter, "_get_k8s_batch_client", return_value=mock_batch):
            with patch.object(adapter, "_get_k8s_core_client", return_value=mock_core):
                result = await adapter._k8s_get_job_status("bioaf-pipeline-42")

        assert result["status"] == "running"
        assert result["pod_name"] == "bioaf-pipeline-42-xyz"


class TestGetJobStatusCompleted:
    @pytest.mark.asyncio
    async def test_completed_status(self, adapter):
        """Test 6: get_job_status returns completed when complete condition is True."""
        mock_batch = MagicMock()

        mock_condition = MagicMock()
        mock_condition.type = "Complete"
        mock_condition.status = "True"

        mock_job = MagicMock()
        mock_job.status.conditions = [mock_condition]
        mock_job.status.active = None
        mock_job.status.succeeded = 1
        mock_job.status.failed = None
        mock_batch.read_namespaced_job.return_value = mock_job

        mock_core = MagicMock()
        mock_pod_list = MagicMock()
        mock_pod_list.items = []
        mock_core.list_namespaced_pod.return_value = mock_pod_list

        with patch.object(adapter, "_get_k8s_batch_client", return_value=mock_batch):
            with patch.object(adapter, "_get_k8s_core_client", return_value=mock_core):
                result = await adapter._k8s_get_job_status("bioaf-pipeline-42")

        assert result["status"] == "completed"


class TestGetJobStatusFailed:
    @pytest.mark.asyncio
    async def test_failed_status(self, adapter):
        """Test 7: get_job_status returns failed when failed condition is True."""
        mock_batch = MagicMock()

        mock_condition = MagicMock()
        mock_condition.type = "Failed"
        mock_condition.status = "True"

        mock_job = MagicMock()
        mock_job.status.conditions = [mock_condition]
        mock_job.status.active = None
        mock_job.status.succeeded = None
        mock_job.status.failed = 1
        mock_batch.read_namespaced_job.return_value = mock_job

        mock_core = MagicMock()
        mock_pod_list = MagicMock()
        mock_pod_list.items = []
        mock_core.list_namespaced_pod.return_value = mock_pod_list

        with patch.object(adapter, "_get_k8s_batch_client", return_value=mock_batch):
            with patch.object(adapter, "_get_k8s_core_client", return_value=mock_core):
                result = await adapter._k8s_get_job_status("bioaf-pipeline-42")

        assert result["status"] == "failed"


class TestGetJobLogs:
    @pytest.mark.asyncio
    async def test_returns_pod_logs(self, adapter):
        """Test 8: get_job_logs returns logs from the pipeline pod."""
        mock_core = MagicMock()

        # Mock pod listing
        mock_pod = MagicMock()
        mock_pod.metadata.name = "bioaf-pipeline-42-xyz"
        mock_pod_list = MagicMock()
        mock_pod_list.items = [mock_pod]
        mock_core.list_namespaced_pod.return_value = mock_pod_list

        # Mock log retrieval
        mock_core.read_namespaced_pod_log.return_value = "Pipeline started\nProcessing sample 1\nDone"

        with patch.object(adapter, "_get_k8s_core_client", return_value=mock_core):
            result = await adapter._k8s_get_job_logs("bioaf-pipeline-42")

        assert "Pipeline started" in result
        assert "Done" in result
        mock_core.read_namespaced_pod_log.assert_called_once()


class TestCancelJob:
    @pytest.mark.asyncio
    async def test_deletes_k8s_job(self, adapter):
        """Test 9: cancel_job deletes the K8s Job with background propagation."""
        mock_batch = MagicMock()
        mock_batch.delete_namespaced_job.return_value = MagicMock()

        with patch.object(adapter, "_get_k8s_batch_client", return_value=mock_batch):
            result = await adapter._k8s_cancel_job("bioaf-pipeline-42")

        mock_batch.delete_namespaced_job.assert_called_once()
        call_kwargs = mock_batch.delete_namespaced_job.call_args[1]
        assert call_kwargs["name"] == "bioaf-pipeline-42"
        assert call_kwargs["namespace"] == "bioaf-pipelines"
        assert call_kwargs["propagation_policy"] == "Background"

        assert result["job_id"] == "bioaf-pipeline-42"
        assert result["status"] == "cancelled"
