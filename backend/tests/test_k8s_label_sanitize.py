"""Tests that K8s job labels are sanitized for validity.

K8s label values must match: (([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9])?
Pipeline names like "nf-core/scrnaseq" contain "/" which is invalid.
The adapter must sanitize label values before creating the Job.
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


class TestLabelSanitization:
    @pytest.mark.asyncio
    async def test_pipeline_name_with_slash_is_sanitized(self, adapter):
        """Pipeline name 'nf-core/scrnaseq' must not appear raw in labels."""
        mock_batch = _mock_batch_client()

        with patch.object(adapter, "_get_k8s_batch_client", return_value=mock_batch):
            await adapter._k8s_submit_job(
                {
                    "run_id": 1,
                    "pipeline_name": "nf-core/scrnaseq",
                    "container_image": "alpine:3.19",
                    "command": ["echo"],
                    "namespace": "bioaf-pipelines",
                    "input_files": [],
                    "parameters": {},
                }
            )

        body = mock_batch.create_namespaced_job.call_args[1]["body"]
        label_value = body["metadata"]["labels"]["bioaf.io/pipeline"]
        # Must not contain "/"
        assert "/" not in label_value
        # Should still be recognizable
        assert "nf-core" in label_value
        assert "scrnaseq" in label_value

    @pytest.mark.asyncio
    async def test_label_value_only_contains_valid_chars(self, adapter):
        """Label values must match K8s regex: alphanumeric, '-', '_', '.'."""
        mock_batch = _mock_batch_client()

        with patch.object(adapter, "_get_k8s_batch_client", return_value=mock_batch):
            await adapter._k8s_submit_job(
                {
                    "run_id": 2,
                    "pipeline_name": "org/pipe@v2.0!beta",
                    "container_image": "alpine:3.19",
                    "command": ["echo"],
                    "namespace": "bioaf-pipelines",
                    "input_files": [],
                    "parameters": {},
                }
            )

        body = mock_batch.create_namespaced_job.call_args[1]["body"]
        label_value = body["metadata"]["labels"]["bioaf.io/pipeline"]

        import re

        assert re.fullmatch(r"([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9]", label_value), (
            f"Label value '{label_value}' does not match K8s label regex"
        )
