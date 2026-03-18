"""Tests for K8s compute adapter building real Nextflow pipeline commands.

When a job_spec includes pipeline_source (nf-core pipeline URL) but no
explicit command, _k8s_submit_job should build a Nextflow run command
with the correct container image, parameters, and sample sheet.
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


class TestNextflowCommandBuilt:
    """When pipeline_source is set and command is empty, build a Nextflow command."""

    @pytest.mark.asyncio
    async def test_uses_nextflow_image_when_pipeline_source_set(self, adapter):
        """Main container should use nextflow image, not alpine."""
        mock_batch = _mock_batch_client()
        job_spec = {
            "run_id": 1,
            "pipeline_name": "nf-core/scrnaseq",
            "pipeline_source": "https://github.com/nf-core/scrnaseq",
            "pipeline_version": "2.7.1",
            "parameters": {"outdir": "/data/results"},
            "sample_sheet": "sample,fastq_1\nS1,s1.fq.gz\n",
            "namespace": "bioaf-pipelines",
            "input_files": ["s1.fq.gz"],
        }

        with patch.object(adapter, "_get_k8s_batch_client", return_value=mock_batch):
            await adapter._k8s_submit_job(job_spec)

        body = mock_batch.create_namespaced_job.call_args[1]["body"]
        main_container = body["spec"]["template"]["spec"]["containers"][0]
        assert "nextflow" in main_container["image"], f"Expected nextflow image, got {main_container['image']}"

    @pytest.mark.asyncio
    async def test_builds_nextflow_run_command(self, adapter):
        """Main container command should run nextflow with the pipeline source."""
        mock_batch = _mock_batch_client()
        job_spec = {
            "run_id": 2,
            "pipeline_name": "nf-core/rnaseq",
            "pipeline_source": "https://github.com/nf-core/rnaseq",
            "pipeline_version": "3.14.0",
            "parameters": {"outdir": "/data/results", "genome": "GRCh38"},
            "sample_sheet": "sample,fastq_1\nS1,s1.fq.gz\n",
            "namespace": "bioaf-pipelines",
            "input_files": [],
        }

        with patch.object(adapter, "_get_k8s_batch_client", return_value=mock_batch):
            await adapter._k8s_submit_job(job_spec)

        body = mock_batch.create_namespaced_job.call_args[1]["body"]
        main_container = body["spec"]["template"]["spec"]["containers"][0]
        command = main_container["command"]
        command_str = " ".join(command)

        assert "nextflow run" in command_str
        assert "nf-core/rnaseq" in command_str
        assert "-r 3.14.0" in command_str

    @pytest.mark.asyncio
    async def test_writes_sample_sheet_to_data_volume(self, adapter):
        """Nextflow command should reference sample sheet at /data/samplesheet.csv."""
        mock_batch = _mock_batch_client()
        job_spec = {
            "run_id": 3,
            "pipeline_name": "nf-core/scrnaseq",
            "pipeline_source": "https://github.com/nf-core/scrnaseq",
            "pipeline_version": "2.7.1",
            "parameters": {},
            "sample_sheet": "sample,fastq_1\nS1,s1.fq.gz\n",
            "namespace": "bioaf-pipelines",
            "input_files": [],
        }

        with patch.object(adapter, "_get_k8s_batch_client", return_value=mock_batch):
            await adapter._k8s_submit_job(job_spec)

        body = mock_batch.create_namespaced_job.call_args[1]["body"]
        pod_spec = body["spec"]["template"]["spec"]

        # Should have an init container that writes the sample sheet
        assert "initContainers" in pod_spec
        init_names = [c["name"] for c in pod_spec["initContainers"]]
        assert "write-samplesheet" in init_names

        # The write-samplesheet init container should write to /data/samplesheet.csv
        writer = next(c for c in pod_spec["initContainers"] if c["name"] == "write-samplesheet")
        writer_cmd = " ".join(writer.get("command", []) + writer.get("args", []))
        assert "/data/samplesheet.csv" in writer_cmd

    @pytest.mark.asyncio
    async def test_passes_parameters_to_nextflow(self, adapter):
        """Pipeline parameters should be passed as --key value args to nextflow."""
        mock_batch = _mock_batch_client()
        job_spec = {
            "run_id": 4,
            "pipeline_name": "nf-core/rnaseq",
            "pipeline_source": "https://github.com/nf-core/rnaseq",
            "pipeline_version": "3.14.0",
            "parameters": {"genome": "GRCh38", "outdir": "/data/results"},
            "sample_sheet": "sample,fastq_1\nS1,s1.fq.gz\n",
            "namespace": "bioaf-pipelines",
            "input_files": [],
        }

        with patch.object(adapter, "_get_k8s_batch_client", return_value=mock_batch):
            await adapter._k8s_submit_job(job_spec)

        body = mock_batch.create_namespaced_job.call_args[1]["body"]
        main_container = body["spec"]["template"]["spec"]["containers"][0]
        command_str = " ".join(main_container["command"])

        assert "--genome GRCh38" in command_str
        assert "--outdir /data/results" in command_str

    @pytest.mark.asyncio
    async def test_explicit_command_overrides_nextflow_build(self, adapter):
        """If command is explicitly set, do not auto-build a Nextflow command."""
        mock_batch = _mock_batch_client()
        job_spec = {
            "run_id": 5,
            "pipeline_name": "bioaf-system-test",
            "pipeline_source": "",
            "command": ["echo", "hello"],
            "container_image": "alpine:3.19",
            "namespace": "bioaf-pipelines",
            "input_files": [],
            "parameters": {},
        }

        with patch.object(adapter, "_get_k8s_batch_client", return_value=mock_batch):
            await adapter._k8s_submit_job(job_spec)

        body = mock_batch.create_namespaced_job.call_args[1]["body"]
        main_container = body["spec"]["template"]["spec"]["containers"][0]
        assert main_container["image"] == "alpine:3.19"
        assert main_container["command"] == ["echo", "hello"]

    @pytest.mark.asyncio
    async def test_includes_input_param_for_samplesheet(self, adapter):
        """Nextflow command should include --input /data/samplesheet.csv."""
        mock_batch = _mock_batch_client()
        job_spec = {
            "run_id": 6,
            "pipeline_name": "nf-core/scrnaseq",
            "pipeline_source": "https://github.com/nf-core/scrnaseq",
            "pipeline_version": "2.7.1",
            "parameters": {},
            "sample_sheet": "sample,fastq_1\nS1,s1.fq.gz\n",
            "namespace": "bioaf-pipelines",
            "input_files": [],
        }

        with patch.object(adapter, "_get_k8s_batch_client", return_value=mock_batch):
            await adapter._k8s_submit_job(job_spec)

        body = mock_batch.create_namespaced_job.call_args[1]["body"]
        main_container = body["spec"]["template"]["spec"]["containers"][0]
        command_str = " ".join(main_container["command"])

        assert "--input /data/samplesheet.csv" in command_str

    @pytest.mark.asyncio
    async def test_default_outdir_when_not_in_parameters(self, adapter):
        """Nextflow command should include --outdir /data/results by default."""
        mock_batch = _mock_batch_client()
        job_spec = {
            "run_id": 7,
            "pipeline_name": "nf-core/scrnaseq",
            "pipeline_source": "https://github.com/nf-core/scrnaseq",
            "pipeline_version": "2.7.1",
            "parameters": {},
            "sample_sheet": "sample,fastq_1\nS1,s1.fq.gz\n",
            "namespace": "bioaf-pipelines",
            "input_files": [],
        }

        with patch.object(adapter, "_get_k8s_batch_client", return_value=mock_batch):
            await adapter._k8s_submit_job(job_spec)

        body = mock_batch.create_namespaced_job.call_args[1]["body"]
        main_container = body["spec"]["template"]["spec"]["containers"][0]
        command_str = " ".join(main_container["command"])

        assert "--outdir /data/results" in command_str
