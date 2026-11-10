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
    @pytest.mark.asyncio
    async def test_collect_outputs_copies_files(self, adapter, temp_dir, monkeypatch):
        monkeypatch.setenv("BIOAF_LOCAL_DATA_ROOT", temp_dir)
        # Need to reimport to pick up env var change
        from app.adapters.storage import gcs

        original_root = gcs.LOCAL_DATA_ROOT
        gcs.LOCAL_DATA_ROOT = temp_dir

        try:
            # Create output files in working dir
            working = os.path.join(temp_dir, "pipeline-output")
            os.makedirs(working)
            with open(os.path.join(working, "results.h5ad"), "w") as f:
                f.write("anndata content")
            with open(os.path.join(working, "umap.png"), "w") as f:
                f.write("plot data")

            collected = await adapter.collect_outputs(working, {"id": 10, "experiment_id": 3})
            assert len(collected) == 2
            filenames = [c.filename for c in collected]
            assert "results.h5ad" in filenames
            assert "umap.png" in filenames

            for item in collected:
                assert "local_path" in item.provider_details
                assert item.storage_uri
                assert item.size_bytes is not None
                assert item.storage_uri.startswith("gs://bioaf-results-testorg/")
        finally:
            gcs.LOCAL_DATA_ROOT = original_root

    @pytest.mark.asyncio
    async def test_collect_outputs_empty_dir(self, adapter, temp_dir, monkeypatch):
        from app.adapters.storage import gcs

        original_root = gcs.LOCAL_DATA_ROOT
        gcs.LOCAL_DATA_ROOT = temp_dir

        try:
            working = os.path.join(temp_dir, "empty-output")
            os.makedirs(working)
            collected = await adapter.collect_outputs(working, {"id": 1, "experiment_id": 1})
            assert collected == []
        finally:
            gcs.LOCAL_DATA_ROOT = original_root


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
