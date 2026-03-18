"""Tests that _k8s_submit_job ensures the namespace exists before creating a job.

The GKE cluster does not come with a bioaf-pipelines namespace. The adapter
must create it (along with the service account and role binding) before
submitting the first job. Without this, jobs fail with a 404 NotFound.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.adapters.compute.kubernetes import KubernetesComputeProvider


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "k8s")
    provider = KubernetesComputeProvider()
    # Start with _namespace_ready = False to simulate first-time use
    provider._namespace_ready = False
    return provider


def _mock_batch_client():
    mock_batch = MagicMock()
    mock_job = MagicMock()
    mock_job.metadata.name = "bioaf-pipeline-1"
    mock_batch.create_namespaced_job.return_value = mock_job
    return mock_batch


class TestNamespaceEnsured:
    @pytest.mark.asyncio
    async def test_submit_job_ensures_namespace_before_creating_job(self, adapter):
        """_k8s_submit_job must call ensure_pipeline_namespace when not ready."""
        mock_batch = _mock_batch_client()

        with patch.object(adapter, "_get_k8s_batch_client", return_value=mock_batch):
            with patch.object(adapter, "ensure_pipeline_namespace") as mock_ensure:
                await adapter._k8s_submit_job(
                    {
                        "run_id": 1,
                        "pipeline_name": "test",
                        "container_image": "alpine:3.19",
                        "command": ["echo", "hello"],
                        "namespace": "bioaf-pipelines",
                        "input_files": [],
                        "parameters": {},
                    }
                )

                mock_ensure.assert_called_once_with("bioaf-pipelines")

    @pytest.mark.asyncio
    async def test_submit_job_skips_ensure_when_namespace_ready(self, adapter):
        """When _namespace_ready is True, skip ensure_pipeline_namespace."""
        adapter._namespace_ready = True
        mock_batch = _mock_batch_client()

        with patch.object(adapter, "_get_k8s_batch_client", return_value=mock_batch):
            with patch.object(adapter, "ensure_pipeline_namespace") as mock_ensure:
                await adapter._k8s_submit_job(
                    {
                        "run_id": 2,
                        "pipeline_name": "test",
                        "container_image": "alpine:3.19",
                        "command": ["echo", "hello"],
                        "namespace": "bioaf-pipelines",
                        "input_files": [],
                        "parameters": {},
                    }
                )

                mock_ensure.assert_not_called()

    @pytest.mark.asyncio
    async def test_submit_job_passes_correct_namespace_to_ensure(self, adapter):
        """ensure_pipeline_namespace receives the namespace from job_spec."""
        mock_batch = _mock_batch_client()

        with patch.object(adapter, "_get_k8s_batch_client", return_value=mock_batch):
            with patch.object(adapter, "ensure_pipeline_namespace") as mock_ensure:
                await adapter._k8s_submit_job(
                    {
                        "run_id": 3,
                        "pipeline_name": "test",
                        "container_image": "alpine:3.19",
                        "command": ["echo"],
                        "namespace": "custom-ns",
                        "input_files": [],
                        "parameters": {},
                    }
                )

                mock_ensure.assert_called_once_with("custom-ns")
