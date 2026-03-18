"""Tests for K8s compute adapter submit_job (spec tests 1-4).

Tests that submit_job creates a correct K8s Job manifest with labels,
node selector, tolerations, init container, and main container.
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


def _mock_k8s_clients(adapter):
    """Create mocked K8s clients for submit_job tests."""
    mock_batch = MagicMock()
    mock_core = MagicMock()

    # Mock the job creation to return a job object
    mock_job = MagicMock()
    mock_job.metadata.name = "bioaf-pipeline-42"
    mock_job.metadata.namespace = "bioaf-pipelines"
    mock_batch.create_namespaced_job.return_value = mock_job

    # Mock pod listing for pod name retrieval
    mock_pod = MagicMock()
    mock_pod.metadata.name = "bioaf-pipeline-42-abc12"
    mock_pod_list = MagicMock()
    mock_pod_list.items = [mock_pod]
    mock_core.list_namespaced_pod.return_value = mock_pod_list

    return mock_batch, mock_core


class TestSubmitJobCreatesK8sJob:
    @pytest.mark.asyncio
    async def test_creates_job_with_correct_labels(self, adapter):
        """Test 1: submit_job creates a K8s Job with correct labels, node selector, tolerations."""
        mock_batch, mock_core = _mock_k8s_clients(adapter)
        job_spec = {
            "run_id": 42,
            "pipeline_name": "nf-core/scrnaseq",
            "container_image": "alpine:3.19",
            "command": ["echo", "hello"],
            "namespace": "bioaf-pipelines",
            "input_files": [],
            "parameters": {},
        }

        with patch.object(adapter, "_get_k8s_batch_client", return_value=mock_batch):
            with patch.object(adapter, "_get_k8s_core_client", return_value=mock_core):
                result = await adapter._k8s_submit_job(job_spec)

        mock_batch.create_namespaced_job.assert_called_once()
        call_kwargs = mock_batch.create_namespaced_job.call_args[1]
        assert call_kwargs["namespace"] == "bioaf-pipelines"

        body = call_kwargs["body"]
        # Check labels
        assert body["metadata"]["labels"]["bioaf.io/pipeline-run"] == "42"
        assert body["metadata"]["labels"]["bioaf.io/pipeline"] == "nf-core-scrnaseq"
        assert body["metadata"]["labels"]["bioaf.io/pool"] == "pipelines"

        # Check node selector and tolerations
        pod_spec = body["spec"]["template"]["spec"]
        assert pod_spec["nodeSelector"]["bioaf.io/pool"] == "pipelines"
        assert any(t["key"] == "bioaf.io/pool" and t["value"] == "pipelines" for t in pod_spec["tolerations"])

        # Check job name
        assert body["metadata"]["name"] == "bioaf-pipeline-42"

        # Check result
        assert result["job_id"] == "bioaf-pipeline-42"
        assert result["namespace"] == "bioaf-pipelines"


class TestSubmitJobInitContainer:
    @pytest.mark.asyncio
    async def test_includes_init_container_for_inputs(self, adapter):
        """Test 2: submit_job includes init container with gsutil commands when inputs provided."""
        mock_batch, mock_core = _mock_k8s_clients(adapter)
        job_spec = {
            "run_id": 43,
            "pipeline_name": "nf-core/scrnaseq",
            "container_image": "alpine:3.19",
            "command": ["echo", "hello"],
            "namespace": "bioaf-pipelines",
            "input_files": [
                {"filename": "sample_R1.fastq.gz", "gcs_uri": "gs://bioaf-raw-demo/sample_R1.fastq.gz"},
                {"filename": "sample_R2.fastq.gz", "gcs_uri": "gs://bioaf-raw-demo/sample_R2.fastq.gz"},
            ],
            "parameters": {},
            "stage_commands": [
                "gsutil cp gs://bioaf-raw-demo/sample_R1.fastq.gz /data/inputs/sample_R1.fastq.gz",
                "gsutil cp gs://bioaf-raw-demo/sample_R2.fastq.gz /data/inputs/sample_R2.fastq.gz",
            ],
        }

        with patch.object(adapter, "_get_k8s_batch_client", return_value=mock_batch):
            with patch.object(adapter, "_get_k8s_core_client", return_value=mock_core):
                await adapter._k8s_submit_job(job_spec)

        body = mock_batch.create_namespaced_job.call_args[1]["body"]
        pod_spec = body["spec"]["template"]["spec"]

        # Should have init containers
        assert "initContainers" in pod_spec
        assert len(pod_spec["initContainers"]) == 1

        init = pod_spec["initContainers"][0]
        assert init["name"] == "stage-inputs"
        assert init["image"] == "google/cloud-sdk:slim"

        # The command should include gsutil cp commands
        command_str = " ".join(init["command"] + init.get("args", []))
        assert "gsutil" in command_str or "gsutil" in str(init)

    @pytest.mark.asyncio
    async def test_no_init_container_without_inputs(self, adapter):
        """Test 3: submit_job has no init container when no input files provided."""
        mock_batch, mock_core = _mock_k8s_clients(adapter)
        job_spec = {
            "run_id": 44,
            "pipeline_name": "bioaf-system-test",
            "container_image": "alpine:3.19",
            "command": ["echo", "hello"],
            "namespace": "bioaf-pipelines",
            "input_files": [],
            "parameters": {},
        }

        with patch.object(adapter, "_get_k8s_batch_client", return_value=mock_batch):
            with patch.object(adapter, "_get_k8s_core_client", return_value=mock_core):
                await adapter._k8s_submit_job(job_spec)

        body = mock_batch.create_namespaced_job.call_args[1]["body"]
        pod_spec = body["spec"]["template"]["spec"]

        init_containers = pod_spec.get("initContainers", [])
        assert len(init_containers) == 0


class TestSubmitJobUpdatesPipelineRun:
    @pytest.mark.asyncio
    async def test_returns_job_metadata(self, adapter):
        """Test 4: submit_job returns job_id, namespace, status, and estimated_cost."""
        mock_batch, mock_core = _mock_k8s_clients(adapter)
        job_spec = {
            "run_id": 45,
            "pipeline_name": "test",
            "container_image": "alpine:3.19",
            "command": ["echo"],
            "namespace": "bioaf-pipelines",
            "input_files": ["a.fq"],
            "parameters": {},
        }

        with patch.object(adapter, "_get_k8s_batch_client", return_value=mock_batch):
            with patch.object(adapter, "_get_k8s_core_client", return_value=mock_core):
                result = await adapter._k8s_submit_job(job_spec)

        assert "job_id" in result
        assert "namespace" in result
        assert "status" in result
        assert result["status"] == "queued"
        assert "estimated_cost" in result
