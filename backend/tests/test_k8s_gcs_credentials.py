"""Tests that pipeline pods get GCS credentials for bucket access.

Nextflow sub-processes need to read FASTQ files from GCS buckets.
The pipeline pod must have GOOGLE_APPLICATION_CREDENTIALS set and
the service account key mounted as a volume.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.adapters.compute.kubernetes import KubernetesComputeProvider


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "k8s")
    provider = KubernetesComputeProvider()
    provider._namespace_ready = True
    provider._cluster_config = {
        "gcp_service_account_key": '{"type": "service_account", "project_id": "test"}',
    }
    return provider


def _mock_batch_client():
    mock_batch = MagicMock()
    mock_job = MagicMock()
    mock_job.metadata.name = "bioaf-pipeline-1"
    mock_batch.create_namespaced_job.return_value = mock_job
    return mock_batch


def _mock_core_client():
    mock_core = MagicMock()
    return mock_core


class TestGCSCredentials:
    @pytest.mark.asyncio
    async def test_pipeline_container_has_gac_env_var(self, adapter):
        """Pipeline container must set GOOGLE_APPLICATION_CREDENTIALS."""
        mock_batch = _mock_batch_client()
        mock_core = _mock_core_client()

        with (
            patch.object(adapter, "_get_k8s_batch_client", return_value=mock_batch),
            patch.object(adapter, "_get_k8s_core_client", return_value=mock_core),
        ):
            await adapter._k8s_submit_job(
                {
                    "run_id": 1,
                    "pipeline_name": "test",
                    "container_image": "alpine:3.19",
                    "command": ["/bin/sh", "-c", "echo hello"],
                    "namespace": "bioaf-pipelines",
                    "input_files": [],
                    "parameters": {},
                }
            )

        body = mock_batch.create_namespaced_job.call_args[1]["body"]
        main_container = body["spec"]["template"]["spec"]["containers"][0]

        env_vars = {e["name"]: e["value"] for e in main_container.get("env", [])}
        assert "GOOGLE_APPLICATION_CREDENTIALS" in env_vars
        assert env_vars["GOOGLE_APPLICATION_CREDENTIALS"] == "/secrets/gcp/key.json"

    @pytest.mark.asyncio
    async def test_pipeline_pod_has_gcp_secret_volume(self, adapter):
        """Pipeline pod must mount the GCP SA key as a volume."""
        mock_batch = _mock_batch_client()
        mock_core = _mock_core_client()

        with (
            patch.object(adapter, "_get_k8s_batch_client", return_value=mock_batch),
            patch.object(adapter, "_get_k8s_core_client", return_value=mock_core),
        ):
            await adapter._k8s_submit_job(
                {
                    "run_id": 1,
                    "pipeline_name": "test",
                    "container_image": "alpine:3.19",
                    "command": ["/bin/sh", "-c", "echo hello"],
                    "namespace": "bioaf-pipelines",
                    "input_files": [],
                    "parameters": {},
                }
            )

        body = mock_batch.create_namespaced_job.call_args[1]["body"]
        pod_spec = body["spec"]["template"]["spec"]

        # Check volume exists
        volume_names = [v["name"] for v in pod_spec["volumes"]]
        assert "gcp-sa-key" in volume_names

        # Check volume mount on main container
        main_container = pod_spec["containers"][0]
        mount_names = [m["name"] for m in main_container["volumeMounts"]]
        assert "gcp-sa-key" in mount_names
