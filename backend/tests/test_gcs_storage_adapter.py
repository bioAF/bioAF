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


class TestGcsResolveInputPath:
    @pytest.mark.asyncio
    async def test_returns_local_path(self, adapter):
        path = await adapter.resolve_input_path({"filename": "sample.fastq.gz"})
        assert path == "/data/inputs/sample.fastq.gz"

    @pytest.mark.asyncio
    async def test_handles_missing_filename(self, adapter):
        path = await adapter.resolve_input_path({})
        assert "unknown" in path


class TestGcsResolveOutputPath:
    @pytest.mark.asyncio
    async def test_returns_local_results_path(self, adapter):
        path = await adapter.resolve_output_path({"id": 42, "experiment_id": 7}, "counts.h5ad")
        assert "experiments/7/pipeline-runs/42/counts.h5ad" in path


class TestGcsStageInputs:
    @pytest.mark.asyncio
    async def test_stage_inputs_creates_files(self, adapter, temp_dir):
        working = os.path.join(temp_dir, "work")
        records = [
            {"filename": "sample_R1.fastq.gz"},
            {"filename": "sample_R2.fastq.gz"},
        ]
        paths = await adapter.stage_inputs(records, working)
        assert len(paths) == 2
        for p in paths:
            assert os.path.exists(p)

    @pytest.mark.asyncio
    async def test_stage_inputs_copies_existing_files(self, adapter, temp_dir):
        # Create a real source file
        src = os.path.join(temp_dir, "source.txt")
        with open(src, "w") as f:
            f.write("real content")

        working = os.path.join(temp_dir, "work")
        records = [{"filename": "source.txt", "local_path": src}]
        paths = await adapter.stage_inputs(records, working)
        assert len(paths) == 1
        with open(paths[0]) as f:
            assert f.read() == "real content"


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
            filenames = [c["filename"] for c in collected]
            assert "results.h5ad" in filenames
            assert "umap.png" in filenames

            for item in collected:
                assert "local_path" in item
                assert "gcs_uri" in item
                assert "size_bytes" in item
                assert item["gcs_uri"].startswith("gs://bioaf-results-testorg/")
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
        assert "buckets" in result
        assert "total_size_gb" in result
        assert "total_cost_monthly_usd" in result
        assert len(result["buckets"]) == 5

    @pytest.mark.asyncio
    async def test_bucket_names_contain_org_slug(self, adapter):
        result = await adapter.get_storage_metrics()
        for bucket in result["buckets"]:
            assert "testorg" in bucket["name"]
            assert "size_gb" in bucket
            assert "object_count" in bucket
            assert "storage_class" in bucket
            assert "cost_monthly_usd" in bucket


class TestGcsBucketProperties:
    def test_ingest_bucket_name(self, adapter):
        assert adapter.ingest_bucket == "bioaf-ingest-testorg"

    def test_raw_bucket_name(self, adapter):
        assert adapter.raw_bucket == "bioaf-raw-testorg"

    def test_working_bucket_name(self, adapter):
        assert adapter.working_bucket == "bioaf-working-testorg"

    def test_results_bucket_name(self, adapter):
        assert adapter.results_bucket == "bioaf-results-testorg"
