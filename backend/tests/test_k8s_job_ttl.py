"""Tests that K8s Jobs have ttlSecondsAfterFinished for log retrieval.

When a pipeline job finishes (success or failure), the pod must stay around
long enough for the pipeline monitor to capture logs before the node scales
back down. ttlSecondsAfterFinished tells K8s to keep the Job and its pods
for a period after completion.
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


def _mock_batch_client():
    mock_batch = MagicMock()
    mock_job = MagicMock()
    mock_job.metadata.name = "bioaf-pipeline-1"
    mock_batch.create_namespaced_job.return_value = mock_job
    return mock_batch


class TestJobTTL:
    @pytest.mark.asyncio
    async def test_job_has_ttl_seconds_after_finished(self, adapter):
        """Job spec must include ttlSecondsAfterFinished for log capture."""
        mock_batch = _mock_batch_client()

        with patch.object(adapter, "_get_k8s_batch_client", return_value=mock_batch):
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

        body = mock_batch.create_namespaced_job.call_args[1]["body"]
        ttl = body["spec"].get("ttlSecondsAfterFinished")
        assert ttl is not None, "Job must set ttlSecondsAfterFinished"
        assert ttl >= 600, f"TTL should be at least 600s (10 min), got {ttl}"
