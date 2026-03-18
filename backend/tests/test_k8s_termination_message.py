"""Tests that K8s Job containers set terminationMessagePolicy.

When a GKE node scales down after a job fails, the kubelet becomes
unavailable and container logs are lost. Setting
terminationMessagePolicy=FallbackToLogsOnError tells K8s to capture
the last ~2KB of stdout/stderr into the pod status object, which
the API server retains even after the node is gone.
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


class TestTerminationMessagePolicy:
    @pytest.mark.asyncio
    async def test_main_container_has_fallback_to_logs_on_error(self, adapter):
        """Pipeline container must set terminationMessagePolicy for log capture."""
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
        main_container = body["spec"]["template"]["spec"]["containers"][0]
        policy = main_container.get("terminationMessagePolicy")
        assert policy == "FallbackToLogsOnError", f"Expected FallbackToLogsOnError, got {policy}"
