"""Tests for the BioAF Adapter Layer (BAL) - base classes, stubs, and registry."""

import pytest

from app.adapters.base import ComputeProvider, NotebookProvider, StorageProvider
from app.adapters.compute.slurm import SlurmComputeProvider
from app.adapters.notebooks.slurm import SlurmNotebookProvider
from app.adapters.storage.nfs import NfsStorageProvider
from app.adapters import registry


# -- Abstract base class tests --


class TestAbstractBaseClasses:
    def test_compute_provider_cannot_be_instantiated(self):
        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            ComputeProvider()  # type: ignore[abstract]

    def test_storage_provider_cannot_be_instantiated(self):
        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            StorageProvider()  # type: ignore[abstract]

    def test_notebook_provider_cannot_be_instantiated(self):
        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            NotebookProvider()  # type: ignore[abstract]


# -- SLURM/NFS stub tests --


class TestSlurmStubs:
    @pytest.fixture
    def slurm_compute(self):
        return SlurmComputeProvider()

    @pytest.fixture
    def slurm_notebook(self):
        return SlurmNotebookProvider()

    @pytest.mark.asyncio
    async def test_submit_job_raises(self, slurm_compute):
        with pytest.raises(NotImplementedError, match="SLURM compute backend coming soon"):
            await slurm_compute.submit_job({})

    @pytest.mark.asyncio
    async def test_cancel_job_raises(self, slurm_compute):
        with pytest.raises(NotImplementedError, match="SLURM compute backend coming soon"):
            await slurm_compute.cancel_job("123")

    @pytest.mark.asyncio
    async def test_get_job_status_raises(self, slurm_compute):
        with pytest.raises(NotImplementedError, match="SLURM compute backend coming soon"):
            await slurm_compute.get_job_status("123")

    @pytest.mark.asyncio
    async def test_list_jobs_raises(self, slurm_compute):
        with pytest.raises(NotImplementedError, match="SLURM compute backend coming soon"):
            await slurm_compute.list_jobs()

    @pytest.mark.asyncio
    async def test_get_job_logs_raises(self, slurm_compute):
        with pytest.raises(NotImplementedError, match="SLURM compute backend coming soon"):
            await slurm_compute.get_job_logs("123")

    @pytest.mark.asyncio
    async def test_get_cluster_status_raises(self, slurm_compute):
        with pytest.raises(NotImplementedError, match="SLURM compute backend coming soon"):
            await slurm_compute.get_cluster_status()

    @pytest.mark.asyncio
    async def test_get_cluster_metrics_raises(self, slurm_compute):
        with pytest.raises(NotImplementedError, match="SLURM compute backend coming soon"):
            await slurm_compute.get_cluster_metrics()

    @pytest.mark.asyncio
    async def test_get_cost_estimate_raises(self, slurm_compute):
        with pytest.raises(NotImplementedError, match="SLURM compute backend coming soon"):
            await slurm_compute.get_cost_estimate({})

    @pytest.mark.asyncio
    async def test_notebook_launch_raises(self, slurm_notebook):
        with pytest.raises(NotImplementedError, match="SLURM compute backend coming soon"):
            await slurm_notebook.launch_session({})

    @pytest.mark.asyncio
    async def test_notebook_terminate_raises(self, slurm_notebook):
        with pytest.raises(NotImplementedError, match="SLURM compute backend coming soon"):
            await slurm_notebook.terminate_session("123")

    @pytest.mark.asyncio
    async def test_notebook_status_raises(self, slurm_notebook):
        with pytest.raises(NotImplementedError, match="SLURM compute backend coming soon"):
            await slurm_notebook.get_session_status("123")

    @pytest.mark.asyncio
    async def test_notebook_list_raises(self, slurm_notebook):
        with pytest.raises(NotImplementedError, match="SLURM compute backend coming soon"):
            await slurm_notebook.list_sessions()

    @pytest.mark.asyncio
    async def test_notebook_connection_raises(self, slurm_notebook):
        with pytest.raises(NotImplementedError, match="SLURM compute backend coming soon"):
            await slurm_notebook.get_connection_command("123")


class TestNfsImplemented:
    """NFS is a real backend as of Phase 7 (no more NotImplementedError stubs).

    Full per-method coverage lives in test_nfs_storage_adapter.py; this just
    confirms the once-stubbed methods now work and capabilities are honest.
    """

    @pytest.fixture
    def nfs_storage(self, tmp_path):
        return NfsStorageProvider(root=str(tmp_path))

    def test_capabilities_declare_no_signed_url(self, nfs_storage):
        assert nfs_storage.capabilities().signed_url_upload is False

    @pytest.mark.asyncio
    async def test_stage_inputs_works(self, nfs_storage, tmp_path):
        staged = await nfs_storage.stage_inputs([{"filename": "x.txt"}], str(tmp_path / "work"))
        assert len(staged) == 1

    @pytest.mark.asyncio
    async def test_get_storage_metrics_works(self, nfs_storage):
        metrics = await nfs_storage.get_storage_metrics()
        assert metrics.total_size_gb >= 0.0


# -- Registry tests --


class TestAdapterRegistry:
    def setup_method(self):
        registry.reset_registry()

    def teardown_method(self):
        registry.reset_registry()

    def test_registry_returns_k8s_adapters_for_kubernetes(self):
        from app.adapters.compute.kubernetes import KubernetesComputeProvider
        from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider
        from app.adapters.storage.gcs import GcsStorageProvider

        registry.initialize_adapters_sync("kubernetes")

        assert isinstance(registry.get_compute_adapter(), KubernetesComputeProvider)
        assert isinstance(registry.get_storage_adapter(), GcsStorageProvider)
        assert isinstance(registry.get_notebook_adapter(), KubernetesNotebookProvider)

    def test_registry_returns_slurm_adapters_for_slurm(self):
        registry.initialize_adapters_sync("slurm")

        assert isinstance(registry.get_compute_adapter(), SlurmComputeProvider)
        assert isinstance(registry.get_storage_adapter(), NfsStorageProvider)
        assert isinstance(registry.get_notebook_adapter(), SlurmNotebookProvider)

    def test_registry_raises_for_unknown_stack(self):
        with pytest.raises(ValueError, match="Unknown compute_stack 'hpc_magic'"):
            registry.initialize_adapters_sync("hpc_magic")

    def test_registry_raises_when_not_initialized(self):
        with pytest.raises(RuntimeError, match="Adapter registry not initialized"):
            registry.get_compute_adapter()

        with pytest.raises(RuntimeError, match="Adapter registry not initialized"):
            registry.get_storage_adapter()

        with pytest.raises(RuntimeError, match="Adapter registry not initialized"):
            registry.get_notebook_adapter()

    def test_reset_clears_adapters(self):
        registry.initialize_adapters_sync("kubernetes")
        assert registry.get_compute_adapter() is not None

        registry.reset_registry()
        with pytest.raises(RuntimeError, match="Adapter registry not initialized"):
            registry.get_compute_adapter()


# -- get_job_report tests --


class TestGetJobReport:
    """ComputeProvider.get_job_report is a base method (BAL rework, Phase 5)."""

    @pytest.mark.asyncio
    async def test_base_default_returns_empty_string(self):
        """A backend without a run-report artifact inherits a soft '' default.

        Promoted to the base in Phase 5 so pipeline_monitor can call it on the
        interface; the SLURM stub (no report) returns '' instead of raising
        AttributeError.
        """
        provider = SlurmComputeProvider()
        assert await provider.get_job_report("job-123") == ""

    @pytest.mark.asyncio
    async def test_k8s_local_returns_empty_report(self):
        """K8s local mode has no GCS report, so returns ''."""
        from app.adapters.compute.kubernetes import KubernetesComputeProvider

        provider = KubernetesComputeProvider()
        assert await provider.get_job_report("local-abc123") == ""


# -- get_job_progress tests --


class TestGetJobProgress:
    """Tests for ComputeProvider.get_job_progress() method."""

    @pytest.mark.asyncio
    async def test_slurm_get_job_progress_raises(self):
        """SLURM stub raises NotImplementedError for get_job_progress."""
        provider = SlurmComputeProvider()
        with pytest.raises(NotImplementedError, match="SLURM compute backend coming soon"):
            await provider.get_job_progress("job-123")

    @pytest.mark.asyncio
    async def test_k8s_local_returns_progress_structure(self):
        """K8s local mode returns a valid normalized progress dict."""
        from app.adapters.compute.kubernetes import KubernetesComputeProvider

        provider = KubernetesComputeProvider()
        result = await provider.get_job_progress("local-abc123")

        assert isinstance(result.percent_complete, (int, float))
        assert isinstance(result.processes, list)

    @pytest.mark.asyncio
    async def test_k8s_local_progress_has_process_fields(self):
        """K8s local mode progress processes have required fields."""
        from app.adapters.compute.kubernetes import KubernetesComputeProvider

        provider = KubernetesComputeProvider()
        result = await provider.get_job_progress("local-abc123")

        for proc in result.processes:
            assert proc.name
            assert proc.status

    @pytest.mark.asyncio
    async def test_abc_requires_get_job_progress(self):
        """ComputeProvider subclass missing get_job_progress cannot instantiate."""

        class IncompleteProvider(ComputeProvider):
            async def submit_job(self, job_spec: dict) -> dict:  # type: ignore[override]
                return {}

            async def cancel_job(self, job_id: str) -> dict:  # type: ignore[override]
                return {}

            async def get_job_status(self, job_id: str) -> dict:  # type: ignore[override]
                return {}

            async def list_jobs(self, filters: dict | None = None) -> list[dict]:  # type: ignore[override]
                return []

            async def get_job_logs(self, job_id: str) -> str:
                return ""

            async def get_cluster_status(self) -> dict:  # type: ignore[override]
                return {}

            async def get_cluster_metrics(self) -> dict:  # type: ignore[override]
                return {}

            async def get_cost_estimate(self, job_spec: dict) -> dict:  # type: ignore[override]
                return {}

            async def get_connection_command(self, job_id: str) -> str:
                return ""

        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            IncompleteProvider()  # type: ignore[abstract]

    def test_parse_trace_to_progress(self):
        """K8s adapter parses Nextflow trace TSV into normalized progress."""
        from app.adapters.compute.kubernetes import KubernetesComputeProvider

        trace = (
            "task_id\tprocess\tstatus\t%cpu\tpeak_rss\trealtime\n"
            "1\tSTARSOLO\tCOMPLETED\t85.2\t4.5 GB\t29m 45s\n"
            "2\tSAMTOOLS_SORT\tRUNNING\t-\t-\t-\n"
            "3\tFASTQC\tCACHED\t20.5\t500 MB\t4m 30s\n"
        )
        result = KubernetesComputeProvider._parse_trace_to_progress(trace)

        assert result["percent_complete"] == 66.7
        assert len(result["processes"]) == 3
        assert result["processes"][0]["name"] == "STARSOLO"
        assert result["processes"][0]["status"] == "completed"
        assert result["processes"][0]["cpu"] == 85.2
        assert result["processes"][0]["memory_gb"] == 4.5
        assert result["processes"][0]["duration_s"] == 1785
        assert result["processes"][1]["status"] == "running"
        assert result["processes"][2]["status"] == "cached"

    def test_nf_command_includes_report_flag(self):
        """Nextflow command includes -with-report when report_gcs_path is set."""
        from app.adapters.compute.kubernetes import KubernetesComputeProvider

        cmd = KubernetesComputeProvider._build_nextflow_command(
            {"pipeline_source": "nf-core/rnaseq", "parameters": {}},
            report_gcs_path="gs://my-bucket/nextflow-reports/bioaf-pipeline-1/report.html",
        )
        shell_cmd = cmd[2]
        assert "-with-report gs://my-bucket/nextflow-reports/bioaf-pipeline-1/report.html" in shell_cmd

    def test_nf_command_omits_report_flag_when_empty(self):
        """Nextflow command omits -with-report when no path is given."""
        from app.adapters.compute.kubernetes import KubernetesComputeProvider

        cmd = KubernetesComputeProvider._build_nextflow_command(
            {"pipeline_source": "nf-core/rnaseq", "parameters": {}},
        )
        shell_cmd = cmd[2]
        assert "-with-report" not in shell_cmd


class TestCloudLoggingFallback:
    """Tests for Cloud Logging fallback in _k8s_get_job_logs."""

    def test_read_cloud_logging_returns_log_entries(self):
        """_read_cloud_logging returns joined log text from Cloud Logging."""
        from unittest.mock import MagicMock, patch

        from app.adapters.compute.kubernetes import KubernetesComputeProvider

        provider = KubernetesComputeProvider()
        provider._cluster_config = {
            "gcp_project_id": "my-project",
            "gcp_service_account_key": '{"type": "service_account"}',
        }

        mock_entry_1 = MagicMock()
        mock_entry_1.payload = "Launching nf-core/scrnaseq"
        mock_entry_2 = MagicMock()
        mock_entry_2.payload = "Process STARSOLO completed"

        mock_client = MagicMock()
        mock_client.list_entries.return_value = [mock_entry_1, mock_entry_2]

        with (
            patch("google.cloud.logging.Client", return_value=mock_client),
            patch(
                "app.adapters.compute.kubernetes._load_gcp_credentials",
                return_value=MagicMock(),
            ),
        ):
            result = provider._read_cloud_logging("bioaf-pipeline-9")

        assert result is not None
        assert "Launching nf-core/scrnaseq" in result
        assert "Process STARSOLO completed" in result
        mock_client.list_entries.assert_called_once()

    def test_read_cloud_logging_returns_none_without_project_id(self):
        """_read_cloud_logging returns None when no GCP project ID is configured."""
        from app.adapters.compute.kubernetes import KubernetesComputeProvider

        provider = KubernetesComputeProvider()
        provider._cluster_config = {}

        result = provider._read_cloud_logging("bioaf-pipeline-9")
        assert result is None

    def test_read_cloud_logging_returns_none_on_empty_results(self):
        """_read_cloud_logging returns None when Cloud Logging has no entries."""
        from unittest.mock import MagicMock, patch

        from app.adapters.compute.kubernetes import KubernetesComputeProvider

        provider = KubernetesComputeProvider()
        provider._cluster_config = {
            "gcp_project_id": "my-project",
            "gcp_service_account_key": '{"type": "service_account"}',
        }

        mock_client = MagicMock()
        mock_client.list_entries.return_value = []

        with (
            patch("google.cloud.logging.Client", return_value=mock_client),
            patch(
                "app.adapters.compute.kubernetes._load_gcp_credentials",
                return_value=MagicMock(),
            ),
        ):
            result = provider._read_cloud_logging("bioaf-pipeline-9")

        assert result is None

    def test_read_cloud_logging_filters_by_pod_name(self):
        """_read_cloud_logging uses the correct filter for the job's pod."""
        from unittest.mock import MagicMock, patch

        from app.adapters.compute.kubernetes import KubernetesComputeProvider

        provider = KubernetesComputeProvider()
        provider._cluster_config = {
            "gcp_project_id": "my-project",
            "gcp_service_account_key": '{"type": "service_account"}',
        }

        mock_client = MagicMock()
        mock_client.list_entries.return_value = []

        with (
            patch("google.cloud.logging.Client", return_value=mock_client),
            patch(
                "app.adapters.compute.kubernetes._load_gcp_credentials",
                return_value=MagicMock(),
            ),
        ):
            provider._read_cloud_logging("bioaf-pipeline-9")

        call_kwargs = mock_client.list_entries.call_args
        filter_str = call_kwargs.kwargs.get("filter_") or call_kwargs[1].get("filter_", "")
        assert "bioaf-pipeline-9" in filter_str
        assert "k8s_container" in filter_str
        assert 'container_name="pipeline"' in filter_str
