"""Tests for QC dashboard metrics extraction and STARsolo parsing."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.qc_dashboard_service import QCDashboardService


STARSOLO_SUMMARY = """Number of Reads,66601887
Reads With Valid Barcodes,0.975795
Sequencing Saturation,0.696901
Q30 Bases in CB+UMI,0.93492
Q30 Bases in RNA read,0.902251
Reads Mapped to Genome: Unique+Multiple,0.956178
Reads Mapped to Genome: Unique,0.875518
Estimated Number of Cells,1158
Mean Reads per Cell,27933
Median Reads per Cell,24457
UMIs in Cells,9376295
Mean UMI per Cell,8096
Median UMI per Cell,7011
Mean Gene per Cell,2281
Median Gene per Cell,2085
Total Gene Detected,24657"""


def test_read_starsolo_summary():
    """Parses STARsolo Gene/Summary.csv into expected metrics."""
    metrics = QCDashboardService._read_starsolo_summary(STARSOLO_SUMMARY)
    assert metrics["cell_count"] == 1158
    assert metrics["median_reads_per_cell"] == 24457.0
    assert metrics["median_genes_per_cell"] == 2085.0
    assert metrics["median_umi_per_cell"] == 7011.0
    assert metrics["saturation"] == pytest.approx(0.696901)


def test_read_starsolo_summary_partial():
    """Handles a Summary.csv with only some fields."""
    partial = "Estimated Number of Cells,500\nMedian Gene per Cell,1200"
    metrics = QCDashboardService._read_starsolo_summary(partial)
    assert metrics["cell_count"] == 500
    assert metrics["median_genes_per_cell"] == 1200.0
    assert metrics["median_reads_per_cell"] is None
    assert metrics["saturation"] is None


def test_read_starsolo_summary_empty():
    """Returns all-None metrics for empty input."""
    metrics = QCDashboardService._read_starsolo_summary("")
    assert metrics["cell_count"] is None


def test_read_starsolo_summary_malformed():
    """Does not crash on malformed input."""
    metrics = QCDashboardService._read_starsolo_summary("not,a,valid\ncsv,file,here")
    assert metrics["cell_count"] is None


def _make_run(output_files: list[str], experiment_id: int = 10, run_id: int = 5):
    """Build a mock PipelineRun with output_files_json."""
    run = MagicMock()
    run.id = run_id
    run.experiment_id = experiment_id
    run.output_files_json = {"files": output_files}
    return run


@pytest.mark.asyncio
async def test_extract_metrics_reads_cached_json():
    """When qc_metrics.json exists in GCS, use it instead of downloading h5ad."""
    cached = {
        "cell_count": 3000,
        "median_genes_per_cell": 1500.0,
        "median_umi_per_cell": 8000.0,
        "mito_pct_median": 3.2,
        "doublet_score_median": None,
        "saturation": None,
        "median_reads_per_cell": None,
    }

    mock_blob = MagicMock()
    mock_blob.exists.return_value = True
    mock_blob.download_as_text.return_value = json.dumps(cached)

    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob

    mock_client = MagicMock()
    mock_client.bucket.return_value = mock_bucket

    mock_session = AsyncMock()

    run = _make_run(["filtered.h5ad"])

    with (
        patch("google.cloud.storage.Client", return_value=mock_client),
        patch.object(
            QCDashboardService,
            "_get_results_bucket",
            return_value="my-results-bucket",
        ),
        patch(
            "app.services.qc_dashboard_service.GcsStorageService.get_credentials",
            return_value=None,
        ),
    ):
        metrics = await QCDashboardService._extract_metrics(mock_session, run)

    assert metrics["cell_count"] == 3000
    assert metrics["median_genes_per_cell"] == 1500.0
    assert metrics["mito_pct_median"] == 3.2


@pytest.mark.asyncio
async def test_extract_metrics_returns_empty_when_no_results_bucket():
    """Returns empty metrics when results bucket is not configured."""
    mock_session = AsyncMock()
    run = _make_run(["filtered.h5ad"])

    with (
        patch.object(
            QCDashboardService,
            "_get_results_bucket",
            return_value=None,
        ),
        patch(
            "app.services.qc_dashboard_service.GcsStorageService.get_credentials",
            return_value=None,
        ),
    ):
        metrics = await QCDashboardService._extract_metrics(mock_session, run)

    assert metrics["cell_count"] is None
    assert metrics["median_genes_per_cell"] is None


def test_compute_quality_rating_good_starsolo():
    """STARsolo metrics with high genes, reads, and saturation rate as good."""
    metrics = {
        "median_genes_per_cell": 2085,
        "median_reads_per_cell": 24457,
        "saturation": 0.697,
        "mito_pct_median": None,
    }
    assert QCDashboardService._compute_quality_rating(metrics) == "good"


def test_compute_quality_rating_excellent_full():
    """Full metrics (including low mito) with high saturation rate as excellent."""
    metrics = {
        "median_genes_per_cell": 2085,
        "median_reads_per_cell": 24457,
        "mito_pct_median": 3.0,
        "saturation": 0.8,
    }
    assert QCDashboardService._compute_quality_rating(metrics) == "excellent"


def test_compute_quality_rating_pending_review_cell_only():
    """Only cell_count available rates as pending_review."""
    metrics = {"cell_count": 1000, "median_genes_per_cell": None, "mito_pct_median": None}
    assert QCDashboardService._compute_quality_rating(metrics) == "pending_review"
