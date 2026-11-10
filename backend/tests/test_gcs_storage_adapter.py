"""Tests for the GCS storage adapter in local mode."""

import os
import tempfile

import pytest

from app.adapters.storage.gcs import GcsStorageProvider


@pytest.fixture(autouse=True)
def set_local_mode(monkeypatch):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "local")


@pytest.fixture
def adapter():
    return GcsStorageProvider(org_slug="testorg")


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


class TestGcsCollectOutputs:
    """Collection lists the run's output URI (plan 17, D2); it used to copy a local directory."""

    @pytest.mark.asyncio
    async def test_collect_outputs_lists_the_run_directory(self, adapter, temp_dir, monkeypatch):
        from app.adapters.storage import gcs

        monkeypatch.setattr(gcs, "LOCAL_DATA_ROOT", temp_dir)
        out = "gs://bioaf-results-testorg/experiments/3/pipeline-runs/10"
        await adapter.write_text(f"{out}/results.h5ad", "anndata content")
        await adapter.write_text(f"{out}/umap.png", "plot data")

        collected = await adapter.collect_outputs(out, {"id": 10, "experiment_id": 3})
        assert sorted(c.filename for c in collected) == ["results.h5ad", "umap.png"]
        for item in collected:
            assert item.storage_uri.startswith(out + "/")
            assert item.size_bytes is not None
            assert await adapter.read_text(item.storage_uri) in ("anndata content", "plot data")

    @pytest.mark.asyncio
    async def test_collect_outputs_empty_dir(self, adapter, temp_dir, monkeypatch):
        from app.adapters.storage import gcs

        monkeypatch.setattr(gcs, "LOCAL_DATA_ROOT", temp_dir)
        collected = await adapter.collect_outputs("gs://bioaf-results-testorg/experiments/1/pipeline-runs/1", {})
        assert collected == []


class TestGcsStorageMetrics:
    @pytest.mark.asyncio
    async def test_metrics_shape(self, adapter):
        result = await adapter.get_storage_metrics()
        assert result.total_size_gb is not None
        assert result.total_cost_monthly_usd is not None
        assert len(result.buckets) == 5

    @pytest.mark.asyncio
    async def test_bucket_names_contain_org_slug(self, adapter):
        result = await adapter.get_storage_metrics()
        for bucket in result.buckets:
            assert "testorg" in bucket.name
            assert bucket.size_gb is not None
            assert bucket.object_count is not None
            assert bucket.storage_class is not None
            assert bucket.cost_monthly_usd is not None


class TestGcsBucketProperties:
    def test_ingest_bucket_name(self, adapter):
        assert adapter.ingest_bucket == "bioaf-ingest-testorg"

    def test_raw_bucket_name(self, adapter):
        assert adapter.raw_bucket == "bioaf-raw-testorg"

    def test_working_bucket_name(self, adapter):
        assert adapter.working_bucket == "bioaf-working-testorg"

    def test_results_bucket_name(self, adapter):
        assert adapter.results_bucket == "bioaf-results-testorg"
