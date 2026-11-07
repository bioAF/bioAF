"""The local-mode BAL adapters' submit, cancel, status, metrics and session flows.

That the pipeline and notebook services reach compute, storage and notebooks through these adapters
is executed in test_service_adapter_calls.py (cancel, logs, completion, stop, idle check),
test_pipeline_run_jobspec.py and test_pipeline_launch_failure.py (launch), test_pipeline_monitor.py
(status sync) and test_notebook_file_mounting.py (notebook launch).
"""

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


class TestComputeAdapterLocalMode:
    @pytest.mark.asyncio
    async def test_adapter_submit_and_cancel_flow(self):
        """End-to-end test of submit -> cancel flow via adapter."""
        adapter = registry.get_compute_adapter()
        assert isinstance(adapter, KubernetesComputeProvider)

        result = await adapter.submit_job({"pipeline": "test", "input_files": ["a.fq"]})
        assert result.status == "queued"
        job_id = result.job_id

        cancel_result = await adapter.cancel_job(job_id)
        assert cancel_result.status == "cancelled"

    @pytest.mark.asyncio
    async def test_adapter_cluster_status(self):
        adapter = registry.get_compute_adapter()
        status = await adapter.get_cluster_status()
        assert status.controller_status == "running"
        assert len(status.node_pools) == 3

    @pytest.mark.asyncio
    async def test_adapter_cluster_metrics(self):
        adapter = registry.get_compute_adapter()
        metrics = await adapter.get_cluster_metrics()
        assert metrics.cost_burn_rate_hourly is not None


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
        assert result.status == "running"

        term = await adapter.terminate_session(result.session_id)
        assert term.status == "stopped"
