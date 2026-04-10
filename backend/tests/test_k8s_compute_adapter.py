"""Tests for the Kubernetes compute adapter in local mode."""

import pytest

from app.adapters.compute.kubernetes import KubernetesComputeProvider


@pytest.fixture(autouse=True)
def set_local_mode(monkeypatch):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "local")


@pytest.fixture
def adapter():
    return KubernetesComputeProvider()


class TestKubernetesComputeSubmitJob:
    @pytest.mark.asyncio
    async def test_submit_job_returns_job_id(self, adapter):
        result = await adapter.submit_job({"pipeline": "nf-core/scrnaseq", "input_files": ["a.fastq.gz"]})
        assert "job_id" in result
        assert result["job_id"].startswith("local-")

    @pytest.mark.asyncio
    async def test_submit_job_returns_queued_status(self, adapter):
        result = await adapter.submit_job({"pipeline": "nf-core/scrnaseq"})
        assert result["status"] == "queued"

    @pytest.mark.asyncio
    async def test_submit_job_returns_cost_estimate(self, adapter):
        result = await adapter.submit_job({"pipeline": "test", "input_files": ["a.fq", "b.fq"]})
        assert "estimated_cost" in result
        assert "estimated_cost_usd" in result["estimated_cost"]

    @pytest.mark.asyncio
    async def test_submit_job_includes_namespace(self, adapter):
        result = await adapter.submit_job({})
        assert result["namespace"] == "bioaf-pipelines"
        assert result["node_pool"] == "bioaf-pipelines"


class TestKubernetesComputeCancelJob:
    @pytest.mark.asyncio
    async def test_cancel_job_returns_cancelled_status(self, adapter):
        result = await adapter.cancel_job("local-abc123")
        assert result["job_id"] == "local-abc123"
        assert result["status"] == "cancelled"
        assert "cancelled_at" in result


class TestKubernetesComputeJobStatus:
    @pytest.mark.asyncio
    async def test_get_job_status_returns_normalized(self, adapter):
        result = await adapter.get_job_status("local-abc123")
        assert result["job_id"] == "local-abc123"
        assert result["status"] in ("queued", "running", "completed", "failed", "cancelled")
        assert "started_at" in result
        assert "exit_code" in result


class TestKubernetesComputeListJobs:
    @pytest.mark.asyncio
    async def test_list_jobs_returns_list(self, adapter):
        result = await adapter.list_jobs()
        assert isinstance(result, list)


class TestKubernetesComputeJobLogs:
    @pytest.mark.asyncio
    async def test_get_job_logs_returns_string(self, adapter):
        result = await adapter.get_job_logs("local-abc123")
        assert isinstance(result, str)
        assert "local mode" in result


class TestKubernetesComputeClusterStatus:
    @pytest.mark.asyncio
    async def test_cluster_status_shape(self, adapter):
        result = await adapter.get_cluster_status()
        assert result["controller_status"] == "running"
        assert "node_pools" in result
        assert "total_nodes" in result
        assert "active_nodes" in result
        assert "queue_depth" in result
        assert "health" in result

    @pytest.mark.asyncio
    async def test_cluster_status_node_pools(self, adapter):
        result = await adapter.get_cluster_status()
        pools = result["node_pools"]
        assert len(pools) == 3
        pool_names = [p["name"] for p in pools]
        assert "bioaf-platform" in pool_names
        assert "bioaf-pipelines" in pool_names
        assert "bioaf-interactive" in pool_names


class TestKubernetesComputeClusterMetrics:
    @pytest.mark.asyncio
    async def test_cluster_metrics_shape(self, adapter):
        result = await adapter.get_cluster_metrics()
        assert "cpu_utilization_pct" in result
        assert "memory_utilization_pct" in result
        assert "cost_burn_rate_hourly" in result
        assert "node_pools" in result

    @pytest.mark.asyncio
    async def test_cluster_metrics_node_pool_detail(self, adapter):
        result = await adapter.get_cluster_metrics()
        for pool in result["node_pools"]:
            assert "name" in pool
            assert "cpu_utilization_pct" in pool
            assert "memory_utilization_pct" in pool
            assert "cost_rate_hourly" in pool


class TestKubernetesComputeCostEstimate:
    @pytest.mark.asyncio
    async def test_cost_estimate_returns_hourly_rate(self, adapter):
        """Cost estimate should return the pipeline pool's hourly node rate."""
        result = await adapter.get_cost_estimate({})
        # n2-highmem-16 on-demand $1.0482/hr * 0.35 spot = ~$0.3669/hr
        assert result["estimated_cost_usd"] == 0.3669
        assert result["currency"] == "USD"

    @pytest.mark.asyncio
    async def test_cost_estimate_basis_describes_node(self, adapter):
        """Basis string should identify the machine type and pricing tier."""
        result = await adapter.get_cost_estimate({})
        assert "n2-highmem-16" in result["basis"]
        assert "spot" in result["basis"]
        assert "$/hr" in result["basis"]

    @pytest.mark.asyncio
    async def test_cost_estimate_same_regardless_of_input_count(self, adapter):
        """Hourly rate doesn't change with input file count."""
        result_0 = await adapter.get_cost_estimate({})
        result_5 = await adapter.get_cost_estimate({"input_files": ["a", "b", "c", "d", "e"]})
        assert result_0["estimated_cost_usd"] == result_5["estimated_cost_usd"]
