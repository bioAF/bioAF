"""Tests that Nextflow command uses K8s executor config and pipeline defaults.

GKE uses containerd (no Docker daemon), so Nextflow must use a generated
nextflow.config with the K8s executor instead of -profile docker.
"""

from app.adapters.compute.kubernetes import KubernetesComputeProvider


class TestNextflowK8sConfig:
    def test_command_uses_config_file_not_docker_profile(self):
        """Nextflow command should use -c /data/nextflow.config, not -profile docker."""
        job_spec = {
            "pipeline_source": "https://github.com/nf-core/scrnaseq",
            "pipeline_version": "2.7.1",
            "parameters": {"outdir": "/data/results"},
            "sample_sheet": "sample,fastq_1\nS1,gs://bucket/R1.fastq.gz\n",
        }

        command = KubernetesComputeProvider._build_nextflow_command(job_spec)

        shell_cmd = command[-1]
        assert "-c /data/nextflow.config" in shell_cmd
        assert "-profile docker" not in shell_cmd

    def test_config_flag_before_params(self):
        """-c /data/nextflow.config should appear before --params in the command."""
        job_spec = {
            "pipeline_source": "https://github.com/nf-core/scrnaseq",
            "pipeline_version": "2.7.1",
            "parameters": {"protocol": "10XV3"},
            "sample_sheet": "header\ndata\n",
        }

        command = KubernetesComputeProvider._build_nextflow_command(job_spec)

        shell_cmd = command[-1]
        config_pos = shell_cmd.index("-c /data/nextflow.config")
        param_pos = shell_cmd.index("--protocol")
        assert config_pos < param_pos
