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

        # Check node selector and tolerations (head pod runs on the on-demand
        # pipeline-head pool, not the Spot pipelines pool -- see
        # TestSubmitJobHeadPoolTargeting for why).
        pod_spec = body["spec"]["template"]["spec"]
        assert pod_spec["nodeSelector"]["bioaf.io/pool"] == "pipeline-head"
        assert any(t["key"] == "bioaf.io/pool" and t["value"] == "pipeline-head" for t in pod_spec["tolerations"])

        # Check job name (includes per-submit suffix for GCS path uniqueness;
        # see TestSubmitJobUniqueJobName for the format contract).
        assert body["metadata"]["name"].startswith("bioaf-pipeline-42-")

        # Check result
        assert result["job_id"].startswith("bioaf-pipeline-42-")
        assert result["namespace"] == "bioaf-pipelines"


class TestSubmitJobUniqueJobName:
    """Two pipeline runs with the same run_id (e.g., after a DB sequence
    reset that recycles IDs) must NOT collide on K8s Job name or GCS
    output paths. The job_name now embeds a per-submit suffix so reports,
    traces, and persisted logs land in distinct prefixes."""

    @pytest.mark.asyncio
    async def test_job_name_has_unique_suffix_beyond_run_id(self, adapter):
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

        body = mock_batch.create_namespaced_job.call_args[1]["body"]
        name = body["metadata"]["name"]
        import re

        assert re.match(r"^bioaf-pipeline-42-\d+$", name), f"job_name must be bioaf-pipeline-42-<suffix>, got {name!r}"
        # K8s Job name length cap is 63 chars.
        assert len(name) <= 63, f"job_name {name!r} exceeds K8s 63-char limit"
        # Returned job_id must match the actual K8s Job name (so it gets
        # persisted to pipeline_runs.k8s_job_name correctly).
        assert result["job_id"] == name

    @pytest.mark.asyncio
    async def test_two_submits_with_same_run_id_produce_distinct_job_names(self, adapter):
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
                with patch("time.time", side_effect=[1700000000.0, 1700000001.0]):
                    result_a = await adapter._k8s_submit_job(job_spec)
                    adapter._namespace_ready = True  # avoid re-entry
                    result_b = await adapter._k8s_submit_job(job_spec)

        assert result_a["job_id"] != result_b["job_id"], (
            "Two submits with the same run_id must produce distinct job_names "
            "so their GCS report/trace paths don't collide"
        )

    @pytest.mark.asyncio
    async def test_gcs_report_and_trace_paths_use_unique_job_name(self, adapter):
        """The Nextflow command's -with-report and -with-trace paths must
        embed the unique job_name suffix so a recycled run_id can't read
        or overwrite a previous run's report."""
        mock_batch, mock_core = _mock_k8s_clients(adapter)
        adapter._cluster_config = {
            "raw_bucket_name": "bioaf-raw-test-abc123",
            "gcp_project_id": "test-proj",
        }
        job_spec = {
            "run_id": 42,
            "pipeline_name": "nf-core/scrnaseq",
            "pipeline_source": "https://github.com/nf-core/scrnaseq",
            "pipeline_version": "2.7.1",
            "namespace": "bioaf-pipelines",
            "input_files": [],
            "parameters": {},
            "sample_sheet": "sample,fastq_1\nS1,gs://bucket/R1.fastq.gz\n",
        }

        async def _fake_read_creds() -> tuple[str, str]:
            return "service_account_key", "{}"

        async def _fake_refresh() -> None:
            return None

        from unittest.mock import patch as _patch

        with (
            _patch.object(adapter, "_get_k8s_batch_client", return_value=mock_batch),
            _patch.object(adapter, "_get_k8s_core_client", return_value=mock_core),
            _patch.object(adapter, "_read_gcp_credentials", _fake_read_creds),
            _patch.object(adapter, "_ensure_cluster_config_fresh", _fake_refresh),
        ):
            result = await adapter._k8s_submit_job(job_spec)

        job_name = result["job_id"]
        body = mock_batch.create_namespaced_job.call_args[1]["body"]
        shell_cmd = body["spec"]["template"]["spec"]["containers"][0]["command"][-1]

        # Both report and trace paths must include the unique job_name (not
        # just the run_id), so two runs with the same run_id can never share
        # output paths.
        assert f"nextflow-reports/{job_name}/report.html" in shell_cmd
        assert f"nextflow-traces/{job_name}/trace.tsv" in shell_cmd


class TestSubmitJobHeadPoolTargeting:
    """The Nextflow head pod runs on the dedicated on-demand bioaf-pipeline-head
    pool to survive Spot preemption that would otherwise kill long pipelines.
    Task pods stay on the cheaper Spot bioaf-pipelines pool (Nextflow's retry
    strategy already handles their preemption via exit codes 143/137/247)."""

    @pytest.mark.asyncio
    async def test_head_job_targets_pipeline_head_pool(self, adapter):
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
                await adapter._k8s_submit_job(job_spec)

        body = mock_batch.create_namespaced_job.call_args[1]["body"]
        pod_spec = body["spec"]["template"]["spec"]

        assert pod_spec["nodeSelector"]["bioaf.io/pool"] == "pipeline-head", (
            "Head Job must target the on-demand pipeline-head pool, not the Spot pipelines pool"
        )

        # And the head pod must tolerate that pool's NoSchedule taint, otherwise
        # the scheduler rejects it.
        tols = pod_spec.get("tolerations", [])
        assert any(
            t.get("key") == "bioaf.io/pool" and t.get("value") == "pipeline-head" and t.get("effect") == "NoSchedule"
            for t in tols
        ), f"Head Job must tolerate bioaf.io/pool=pipeline-head:NoSchedule, got {tols}"


class TestSubmitJobAutoscalerAnnotation:
    """Without cluster-autoscaler.kubernetes.io/safe-to-evict=false on the head
    pod, GKE's autoscaler may scale down the node mid-pipeline. Nextflow's
    head pod is the workflow coordinator -- if it gets evicted, every running
    task is killed and the pipeline fails. This was the failure mode behind
    the v0.11.12 STAR_GENOMEGENERATE eviction at 80% progress."""

    @pytest.mark.asyncio
    async def test_head_pod_template_has_safe_to_evict_false(self, adapter):
        """Head Job's pod template must carry the safe-to-evict=false annotation."""
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
                await adapter._k8s_submit_job(job_spec)

        body = mock_batch.create_namespaced_job.call_args[1]["body"]
        pod_template_meta = body["spec"]["template"].get("metadata", {})
        annotations = pod_template_meta.get("annotations", {})
        assert annotations.get("cluster-autoscaler.kubernetes.io/safe-to-evict") == "false", (
            "Head pod must be pinned with safe-to-evict=false to survive autoscaler scale-down"
        )


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
