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
    async def test_k8s_config_pins_task_pods_to_the_tainted_pipelines_pool(self, adapter):
        """The bioaf-pipelines pool is tainted (cost-leak fix), so task pods must carry the matching
        toleration plus a nodeSelector pinning them to the pool. Nextflow 25.10 supports these k8s.pod
        options (an earlier assumption that it did not is why the pool used to be left untainted)."""
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

        # The generated k8s.pod config pins task pods to the tainted pipelines pool.
        assert "nodeSelector: 'bioaf.io/pool=pipelines'" in config_script
        assert "toleration: [key: 'bioaf.io/pool'" in config_script

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

    def test_k8s_config_pins_task_pods_against_autoscaler_eviction(self):
        """Long-running task pods (STAR_GENOMEGENERATE, alignment, etc.) must
        carry cluster-autoscaler.kubernetes.io/safe-to-evict=false so the
        autoscaler doesn't scale down their node mid-task. Otherwise a 30-45
        minute STAR run on its own node looks 'underutilized' to the
        autoscaler and gets terminated."""
        config = KubernetesComputeProvider._build_nextflow_k8s_config(
            namespace="bioaf-pipelines",
            has_gcs_secret=True,
            gcs_work_dir="gs://bioaf-raw-test-abc123/nextflow-work",
        )
        # The annotation must appear inside the k8s.pod directive list.
        assert "cluster-autoscaler.kubernetes.io/safe-to-evict" in config
        assert "'false'" in config
        # Sanity: it should be under k8s.pod, not loose in the file.
        k8s_pod_line = [line for line in config.splitlines() if line.startswith("k8s.pod")]
        assert k8s_pod_line, "k8s.pod directive must exist when annotation is present"
        assert "safe-to-evict" in k8s_pod_line[0]

    def test_k8s_config_no_wave_fusion_without_gcs(self):
        """Wave/Fusion should not be enabled when no GCS work dir is set."""
        config = KubernetesComputeProvider._build_nextflow_k8s_config(
            namespace="bioaf-pipelines",
            has_gcs_secret=True,
            gcs_work_dir=None,
        )
        assert "wave.enabled" not in config
        assert "fusion.enabled" not in config

    def test_k8s_config_forces_multiqc_to_export_static_plots(self):
        """Newer MultiQC versions no longer export PNGs by default, so the
        nf-core MULTIQC process must be invoked with --export. We inject this
        via a process selector in nextflow.config so every pipeline run that
        invokes MULTIQC produces multiqc_plots/png/, which the QC dashboard
        plot collector reads from GCS.

        Without this, the QC dashboard plot grid is empty -- not because the
        pipeline failed, but because MultiQC stopped writing PNGs by default."""
        config = KubernetesComputeProvider._build_nextflow_k8s_config(
            namespace="bioaf-pipelines",
            has_gcs_secret=True,
            gcs_work_dir="gs://bioaf-raw-test-abc123/nextflow-work",
        )
        # Must scope the override to the MULTIQC process so it does not affect
        # other tasks.
        assert "withName: 'MULTIQC'" in config
        # Must pass --export through to MultiQC.
        assert "--export" in config

    def test_k8s_config_multiqc_ext_args_not_self_referential(self):
        """The MULTIQC ext.args override must not read task.ext.args back into
        itself. A closure like `ext.args = { (task.ext.args ?: '') + ' --export' }`
        is self-referential: at resolution time `task.ext.args` *is* that closure,
        so evaluating it recurses forever and Nextflow aborts the MULTIQC process
        with `java.lang.StackOverflowError` before it ever runs. We override
        ext.args with a plain value instead.

        Regression: a self-referential closure shipped in the generated config
        and crashed every scrnaseq run at the MULTIQC step."""
        config = KubernetesComputeProvider._build_nextflow_k8s_config(
            namespace="bioaf-pipelines",
            has_gcs_secret=True,
            gcs_work_dir="gs://bioaf-raw-test-abc123/nextflow-work",
        )
        multiqc_line = next(line for line in config.splitlines() if "withName: 'MULTIQC'" in line)
        # The override must still inject --export...
        assert "--export" in multiqc_line
        # ...but must not reference task.ext.args, which is the self-reference
        # that caused the StackOverflowError.
        assert "task.ext.args" not in multiqc_line

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


# --- what run 43 cost us (findings-05 section 15) ------------------------------------------------


def _config(**kw):
    from app.adapters.compute.kubernetes import KubernetesComputeProvider

    kw.setdefault("namespace", "bioaf-pipelines")
    kw.setdefault("has_gcs_secret", False)
    return KubernetesComputeProvider._build_nextflow_k8s_config(**kw)


def test_task_pods_declare_the_disk_they_need():
    """Every alignment in run 43 was evicted: "The node was low on resource: ephemeral-storage ...
    Container was using 80520660Ki, **request is 0**". Requesting nothing means the scheduler places
    two 80 GB tasks on one 100 GB node and kubelet kills whichever is largest, three hours in.

    Nextflow's `disk` directive is what the k8s executor turns into an ephemeral-storage request, and
    it has never been set."""
    cfg = _config(pipeline_machine_type="n2-highmem-16")
    assert "process.disk" in cfg


def test_the_disk_request_scales_with_the_pool_it_will_run_on():
    """A fixed request is wrong in both directions: too large and small steps stop scheduling, too
    small and the packing that caused the eviction comes straight back. It has to follow the node's
    actual disk, which is now configurable."""
    small = _config(pipeline_machine_type="n2-highmem-16", pipeline_disk_gb=100)
    large = _config(pipeline_machine_type="n2-highmem-16", pipeline_disk_gb=500)

    def caps(cfg):
        """(ceiling, first-attempt request) out of `Math.min(<cap>, <base> * task.attempt)`."""
        import re

        m = re.search(r"process\.disk = .*Math\.min\((\d+), (\d+) \* task\.attempt\)", cfg)
        assert m, "unexpected disk directive: " + cfg
        return int(m.group(1)), int(m.group(2))

    assert caps(large) > caps(small)
    # Never the whole disk: the OS, the container images and the kubelet threshold all live there
    # too, and a request that exceeds allocatable schedules nothing at all.
    cap_small, base_small = caps(small)
    assert cap_small < 100
    assert base_small < cap_small


def test_a_retry_asks_for_more_disk_than_the_attempt_that_was_evicted():
    """137 was read as "Spot preemption: retry without escalating", and a disk eviction exits 137
    too. Nextflow cannot see the pod's termination REASON, so the two cannot be told apart from the
    exit status -- which is exactly why retrying unchanged was wrong: run 43 re-ran the same 3-hour
    alignment into the same full disk, three times.

    Escalating the disk request on each attempt resolves both cases without needing to tell them
    apart. An evicted task comes back asking for more room, so the scheduler gives it a node to
    itself; a genuinely preempted task is unaffected, because a larger request on a healthy node
    costs nothing. This is the same shape as the memory escalation nf-core already relies on."""
    cfg = _config(pipeline_machine_type="n2-highmem-16", pipeline_disk_gb=500)
    disk_line = next(line for line in cfg.splitlines() if line.startswith("process.disk"))
    assert "task.attempt" in disk_line, "a retry asks for the same disk that was just evicted: " + disk_line
