"""Tests verifying that refactored services call through the BAL adapter interfaces."""

import pytest

from app.adapters import registry
from app.adapters.compute.kubernetes import KubernetesComputeProvider
from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider
from app.adapters.storage.gcs import GcsStorageProvider


@pytest.fixture(autouse=True)
def init_registry(monkeypatch):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "local")
    registry.reset_registry()
    registry.initialize_adapters_sync("kubernetes")
    yield
    registry.reset_registry()


class TestPipelineRunServiceUsesAdapter:
    @pytest.mark.asyncio
    async def test_launch_run_calls_compute_adapter(self):
        """Verify pipeline_run_service imports from adapters.registry."""
        from app.services.pipeline_run_service import PipelineRunService

        # Verify the module references the adapter, not SlurmService directly
        import inspect

        source = inspect.getsource(PipelineRunService.launch_run)
        assert "get_compute_adapter" in source
        assert "SlurmService._run_ssh_command" not in source

    @pytest.mark.asyncio
    async def test_cancel_run_calls_compute_adapter(self):
        """Verify cancel_run uses the compute adapter."""
        from app.services.pipeline_run_service import PipelineRunService

        import inspect

        source = inspect.getsource(PipelineRunService.cancel_run)
        assert "get_compute_adapter" in source
        assert "SlurmService._run_ssh_command" not in source


class TestPipelineMonitorServiceUsesAdapter:
    @pytest.mark.asyncio
    async def test_sync_uses_compute_adapter(self):
        """Verify pipeline_monitor_service imports from adapters.registry."""
        from app.services.pipeline_monitor_service import PipelineMonitorService

        import inspect

        source = inspect.getsource(PipelineMonitorService._sync_single_run)
        assert "get_compute_adapter" in source

    @pytest.mark.asyncio
    async def test_get_run_logs_uses_adapter(self):
        from app.services.pipeline_monitor_service import PipelineMonitorService

        import inspect

        source = inspect.getsource(PipelineMonitorService.get_run_logs)
        assert "get_compute_adapter" in source

    @pytest.mark.asyncio
    async def test_handle_completion_uses_storage_adapter(self):
        from app.services.pipeline_monitor_service import PipelineMonitorService

        import inspect

        source = inspect.getsource(PipelineMonitorService._handle_completion)
        assert "get_storage_adapter" in source


class TestNotebookServiceUsesAdapter:
    @pytest.mark.asyncio
    async def test_launch_session_calls_notebook_adapter(self):
        """Verify notebook_service uses the notebook adapter."""
        from app.services.notebook_service import NotebookService

        import inspect

        source = inspect.getsource(NotebookService.launch_session)
        assert "get_notebook_adapter" in source
        assert "SlurmService" not in source

    @pytest.mark.asyncio
    async def test_stop_session_calls_notebook_adapter(self):
        from app.services.notebook_service import NotebookService

        import inspect

        source = inspect.getsource(NotebookService.stop_session)
        assert "get_notebook_adapter" in source

    @pytest.mark.asyncio
    async def test_check_idle_uses_notebook_adapter(self):
        from app.services.notebook_service import NotebookService

        import inspect

        source = inspect.getsource(NotebookService.check_idle_sessions)
        assert "get_notebook_adapter" in source
        assert "SlurmService" not in source


class TestComputeAdapterLocalMode:
    @pytest.mark.asyncio
    async def test_adapter_submit_and_cancel_flow(self):
        """End-to-end test of submit -> cancel flow via adapter."""
        adapter = registry.get_compute_adapter()
        assert isinstance(adapter, KubernetesComputeProvider)

        result = await adapter.submit_job({"pipeline": "test", "input_files": ["a.fq"]})
        assert result["status"] == "queued"
        job_id = result["job_id"]

        cancel_result = await adapter.cancel_job(job_id)
        assert cancel_result["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_adapter_cluster_status(self):
        adapter = registry.get_compute_adapter()
        status = await adapter.get_cluster_status()
        assert status["controller_status"] == "running"
        assert len(status["node_pools"]) == 3

    @pytest.mark.asyncio
    async def test_adapter_cluster_metrics(self):
        adapter = registry.get_compute_adapter()
        metrics = await adapter.get_cluster_metrics()
        assert "cost_burn_rate_hourly" in metrics


class TestStorageAdapterLocalMode:
    @pytest.mark.asyncio
    async def test_storage_metrics(self):
        adapter = registry.get_storage_adapter()
        assert isinstance(adapter, GcsStorageProvider)
        metrics = await adapter.get_storage_metrics()
        assert metrics.total_size_gb is not None


class TestNotebookAdapterLocalMode:
    @pytest.mark.asyncio
    async def test_notebook_launch_terminate_flow(self):
        adapter = registry.get_notebook_adapter()
        assert isinstance(adapter, KubernetesNotebookProvider)

        result = await adapter.launch_session({"session_type": "jupyter"})
        assert result["status"] == "running"

        term = await adapter.terminate_session(result["session_id"])
        assert term["status"] == "stopped"
