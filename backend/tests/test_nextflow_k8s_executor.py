"""Tests that Nextflow uses the K8s executor instead of Docker profile.

GKE nodes use containerd, not Docker. Nextflow must use the K8s executor
so each process runs as its own K8s pod rather than trying to spawn
Docker containers inside the pipeline pod.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.adapters.compute.kubernetes import KubernetesComputeProvider


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "k8s")
    provider = KubernetesComputeProvider()
    provider._namespace_ready = True
    sa_key = '{"type": "service_account", "project_id": "test"}'
    provider._cluster_config = {
        "gcp_service_account_key": sa_key,
        "raw_bucket_name": "bioaf-raw-test-abc123",
    }

    async def _fake_read_creds() -> tuple[str, str]:
        return "service_account_key", sa_key

    monkeypatch.setattr(provider, "_read_gcp_credentials", _fake_read_creds)
    return provider


def _mock_batch_client():
    mock_batch = MagicMock()
    mock_job = MagicMock()
    mock_job.metadata.name = "bioaf-pipeline-1"
    mock_batch.create_namespaced_job.return_value = mock_job
    return mock_batch


def _mock_core_client():
    return MagicMock()


class TestK8sExecutor:
    def test_command_does_not_include_profile_docker(self):
        """Nextflow command must NOT use -profile docker on GKE."""
        job_spec = {
            "pipeline_source": "https://github.com/nf-core/scrnaseq",
            "pipeline_version": "2.7.1",
            "parameters": {"outdir": "/data/results"},
            "sample_sheet": "sample,fastq_1\nS1,gs://bucket/R1.fastq.gz\n",
        }

        command = KubernetesComputeProvider._build_nextflow_command(job_spec)
        shell_cmd = command[-1]
        assert "-profile docker" not in shell_cmd

    def test_command_references_k8s_config_file(self):
        """Nextflow command should use -c /data/nextflow.config for K8s executor."""
        job_spec = {
            "pipeline_source": "https://github.com/nf-core/scrnaseq",
            "pipeline_version": "2.7.1",
            "parameters": {"outdir": "/data/results"},
            "sample_sheet": "sample,fastq_1\nS1,gs://bucket/R1.fastq.gz\n",
        }

        command = KubernetesComputeProvider._build_nextflow_command(job_spec)
        shell_cmd = command[-1]
        assert "-c /data/nextflow.config" in shell_cmd

    @pytest.mark.asyncio
    async def test_init_container_writes_nextflow_config(self, adapter):
        """An init container must write nextflow.config with K8s executor settings."""
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
                    "pipeline_source": "https://github.com/nf-core/scrnaseq",
                    "pipeline_version": "2.7.1",
                    "container_image": "nextflow/nextflow:24.04.4",
                    "namespace": "bioaf-pipelines",
                    "input_files": [],
                    "parameters": {},
                    "sample_sheet": "sample,fastq_1\nS1,gs://bucket/R1.fastq.gz\n",
                }
            )

        body = mock_batch.create_namespaced_job.call_args[1]["body"]
        pod_spec = body["spec"]["template"]["spec"]
        init_containers = pod_spec.get("initContainers", [])

        # Find the config-writer init container
        config_writers = [ic for ic in init_containers if ic["name"] == "write-nf-config"]
        assert len(config_writers) == 1, "Expected a write-nf-config init container"

        config_script = config_writers[0]["command"][-1]
        assert "process.executor" in config_script
        assert "'k8s'" in config_script
        assert "bioaf-pipelines" in config_script
        assert "bioaf-pipeline-runner" in config_script

    @pytest.mark.asyncio
    async def test_k8s_config_includes_gcs_credentials(self, adapter):
        """K8s executor config must propagate GCS credentials to spawned pods."""
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
                    "pipeline_source": "https://github.com/nf-core/scrnaseq",
                    "pipeline_version": "2.7.1",
                    "namespace": "bioaf-pipelines",
                    "input_files": [],
                    "parameters": {},
                    "sample_sheet": "sample,fastq_1\nS1,gs://bucket/R1.fastq.gz\n",
                }
            )

        body = mock_batch.create_namespaced_job.call_args[1]["body"]
        pod_spec = body["spec"]["template"]["spec"]
        init_containers = pod_spec.get("initContainers", [])

        config_writers = [ic for ic in init_containers if ic["name"] == "write-nf-config"]
        config_script = config_writers[0]["command"][-1]

        # K8s executor pods need the GCS secret mounted
        assert "gcp-sa-key" in config_script or "bioaf-gcs-sa-key" in config_script
        assert "GOOGLE_APPLICATION_CREDENTIALS" in config_script

    @pytest.mark.asyncio
    async def test_k8s_config_omits_unsupported_tolerations(self, adapter):
        """Nextflow K8s executor does not support tolerations in k8s.pod."""
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
                    "pipeline_source": "https://github.com/nf-core/scrnaseq",
                    "pipeline_version": "2.7.1",
                    "namespace": "bioaf-pipelines",
                    "input_files": [],
                    "parameters": {},
                    "sample_sheet": "sample,fastq_1\nS1,gs://bucket/R1.fastq.gz\n",
                }
            )

        body = mock_batch.create_namespaced_job.call_args[1]["body"]
        pod_spec = body["spec"]["template"]["spec"]
        init_containers = pod_spec.get("initContainers", [])

        config_writers = [ic for ic in init_containers if ic["name"] == "write-nf-config"]
        config_script = config_writers[0]["command"][-1]

        # Nextflow doesn't support tolerations or nodeSelector in k8s.pod
        assert "tolerations" not in config_script
        assert "nodeSelector" not in config_script

    def test_k8s_config_sets_gcs_work_dir(self):
        """Nextflow workDir must point to GCS so head and process pods share files."""
        config = KubernetesComputeProvider._build_nextflow_k8s_config(
            namespace="bioaf-pipelines",
            has_gcs_secret=True,
            gcs_work_dir="gs://bioaf-raw-test-abc123/nextflow-work",
        )
        assert "workDir = 'gs://bioaf-raw-test-abc123/nextflow-work'" in config

    def test_k8s_config_enables_wave_and_fusion_for_gcs(self):
        """Wave + Fusion must be enabled when GCS work dir is set."""
        config = KubernetesComputeProvider._build_nextflow_k8s_config(
            namespace="bioaf-pipelines",
            has_gcs_secret=True,
            gcs_work_dir="gs://bioaf-raw-test-abc123/nextflow-work",
        )
        assert "wave.enabled = true" in config
        assert "fusion.enabled = true" in config
        assert "fusion.exportStorageCredentials = true" in config

    def test_k8s_config_no_wave_fusion_without_gcs(self):
        """Wave/Fusion should not be enabled when no GCS work dir is set."""
        config = KubernetesComputeProvider._build_nextflow_k8s_config(
            namespace="bioaf-pipelines",
            has_gcs_secret=True,
            gcs_work_dir=None,
        )
        assert "wave.enabled" not in config
        assert "fusion.enabled" not in config

    def test_command_logs_config_before_run(self):
        """Nextflow command should cat the config file for diagnostic logging."""
        job_spec = {
            "pipeline_source": "https://github.com/nf-core/scrnaseq",
            "pipeline_version": "2.7.1",
            "parameters": {"outdir": "/data/results"},
            "sample_sheet": "sample,fastq_1\nS1,gs://bucket/R1.fastq.gz\n",
        }
        command = KubernetesComputeProvider._build_nextflow_command(job_spec)
        shell_cmd = command[-1]
        assert "cat /data/nextflow.config" in shell_cmd
        # cat must come before the nextflow run
        cat_pos = shell_cmd.index("cat /data/nextflow.config")
        nf_pos = shell_cmd.index("nextflow run")
        assert cat_pos < nf_pos
