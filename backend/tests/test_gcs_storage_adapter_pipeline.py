"""Tests for GCS storage adapter pipeline operations (spec tests 12-15).

Tests collect_outputs for real GCS mode.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.adapters.storage.gcs import GcsStorageProvider


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "k8s")
    return GcsStorageProvider(org_slug="testorg")


class TestCollectOutputsRegistersFiles:
    @pytest.mark.asyncio
    async def test_registers_files_in_db(self, adapter):
        """Test 13: collect_outputs lists GCS objects and creates file records."""
        mock_storage_client = MagicMock()
        mock_bucket = MagicMock()
        mock_storage_client.bucket.return_value = mock_bucket

        # Mock GCS blob listing
        mock_blob1 = MagicMock()
        mock_blob1.name = "experiments/7/pipeline-runs/42/test_result.json"
        mock_blob1.size = 256
        mock_blob1.md5_hash = "abc123hash=="

        mock_blob2 = MagicMock()
        mock_blob2.name = "experiments/7/pipeline-runs/42/input_manifest.txt"
        mock_blob2.size = 64
        mock_blob2.md5_hash = "def456hash=="

        mock_bucket.list_blobs.return_value = [mock_blob1, mock_blob2]

        with patch.object(adapter, "_get_gcs_client", return_value=mock_storage_client):
            result = await adapter._gcs_collect_outputs(
                "gs://bioaf-results-testorg/experiments/7/pipeline-runs/42/",
                {"id": 42, "experiment_id": 7},
            )

        assert len(result) == 2
        filenames = [r["filename"] for r in result]
        assert "test_result.json" in filenames
        assert "input_manifest.txt" in filenames


class TestCollectOutputsOrganizesByExperiment:
    @pytest.mark.asyncio
    async def test_files_under_experiment_prefix(self, adapter):
        """Test 14: collect_outputs places files under experiments/{id}/pipeline-runs/{run_id}/."""
        mock_storage_client = MagicMock()
        mock_bucket = MagicMock()
        mock_storage_client.bucket.return_value = mock_bucket

        mock_blob = MagicMock()
        mock_blob.name = "experiments/7/pipeline-runs/42/output.csv"
        mock_blob.size = 128
        mock_blob.md5_hash = "hash=="
        mock_bucket.list_blobs.return_value = [mock_blob]

        with patch.object(adapter, "_get_gcs_client", return_value=mock_storage_client):
            result = await adapter._gcs_collect_outputs(
                "gs://bioaf-results-testorg/experiments/7/pipeline-runs/42/",
                {"id": 42, "experiment_id": 7},
            )

        assert len(result) == 1
        assert "experiments/7/pipeline-runs/42/" in result[0]["gcs_uri"]


class TestCollectOutputsComputesChecksums:
    @pytest.mark.asyncio
    async def test_stores_md5_checksums(self, adapter):
        """Test 15: collect_outputs computes and stores MD5 checksums."""
        mock_storage_client = MagicMock()
        mock_bucket = MagicMock()
        mock_storage_client.bucket.return_value = mock_bucket

        mock_blob = MagicMock()
        mock_blob.name = "experiments/7/pipeline-runs/42/result.h5ad"
        mock_blob.size = 1024
        mock_blob.md5_hash = "rL0Y20zC+Fzt72VPzMSk2A=="
        mock_bucket.list_blobs.return_value = [mock_blob]

        with patch.object(adapter, "_get_gcs_client", return_value=mock_storage_client):
            result = await adapter._gcs_collect_outputs(
                "gs://bioaf-results-testorg/experiments/7/pipeline-runs/42/",
                {"id": 42, "experiment_id": 7},
            )

        assert len(result) == 1
        assert result[0]["md5_hash"] == "rL0Y20zC+Fzt72VPzMSk2A=="
        assert result[0]["size_bytes"] == 1024
