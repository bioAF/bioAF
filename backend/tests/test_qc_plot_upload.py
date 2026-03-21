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

    # New fields from STARsolo Summary.csv
    assert metrics["number_of_reads"] == 66601887
    assert metrics["valid_barcodes"] == pytest.approx(0.975795)
    assert metrics["q30_bases_barcode"] == pytest.approx(0.93492)
    assert metrics["q30_bases_rna_read"] == pytest.approx(0.902251)
    assert metrics["reads_mapped_genome"] == pytest.approx(0.956178)
    assert metrics["reads_mapped_genome_unique"] == pytest.approx(0.875518)
    assert metrics["mean_reads_per_cell"] == 27933.0
    assert metrics["mean_umi_per_cell"] == 8096.0
    assert metrics["mean_genes_per_cell"] == 2281.0
    assert metrics["total_genes_detected"] == 24657
    assert metrics["umis_in_cells"] == 9376295


def test_read_starsolo_summary_new_fields_default_none():
    """New fields default to None when not present in partial input."""
    partial = "Estimated Number of Cells,500\nMedian Gene per Cell,1200"
    metrics = QCDashboardService._read_starsolo_summary(partial)
    assert metrics["cell_count"] == 500
    assert metrics["median_genes_per_cell"] == 1200.0
    assert metrics["number_of_reads"] is None
    assert metrics["valid_barcodes"] is None
    assert metrics["q30_bases_barcode"] is None
    assert metrics["q30_bases_rna_read"] is None
    assert metrics["reads_mapped_genome"] is None
    assert metrics["reads_mapped_genome_unique"] is None
    assert metrics["mean_reads_per_cell"] is None
    assert metrics["mean_umi_per_cell"] is None
    assert metrics["mean_genes_per_cell"] is None
    assert metrics["total_genes_detected"] is None
    assert metrics["umis_in_cells"] is None


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


def test_compute_quality_rating_excellent_with_new_metrics():
    """High genes + reads + saturation + mapping + Q30 + valid barcodes rates excellent."""
    metrics = {
        "median_genes_per_cell": 2085,
        "median_reads_per_cell": 24457,
        "saturation": 0.8,
        "mito_pct_median": None,
        "reads_mapped_genome": 0.956,
        "q30_bases_rna_read": 0.902,
        "valid_barcodes": 0.976,
    }
    assert QCDashboardService._compute_quality_rating(metrics) == "excellent"


def test_compute_quality_rating_concerning_low_mapping():
    """Very low mapping rate is concerning regardless of other metrics."""
    metrics = {
        "median_genes_per_cell": 2085,
        "median_reads_per_cell": 24457,
        "saturation": 0.8,
        "mito_pct_median": None,
        "reads_mapped_genome": 0.3,
    }
    assert QCDashboardService._compute_quality_rating(metrics) == "concerning"


# ---------------------------------------------------------------------------
# Barcode rank (knee) plot data extraction
# ---------------------------------------------------------------------------


def test_build_barcode_rank_data_basic():
    """Builds downsampled barcode rank curve from sorted UMI counts."""
    # Simulate 100 barcodes with descending UMI counts
    umi_counts = list(range(1000, 0, -10))  # [1000, 990, 980, ..., 10]
    result = QCDashboardService._build_barcode_rank_data(umi_counts)

    assert isinstance(result, list)
    assert len(result) > 0
    # Each point is [rank, umi_count]
    assert len(result[0]) == 2
    # First point should be rank 1 with highest UMI count
    assert result[0][0] == 1
    assert result[0][1] == 1000
    # Last point rank should equal total barcodes
    assert result[-1][0] == len(umi_counts)


def test_build_barcode_rank_data_downsamples_large_input():
    """Downsamples to max_points when input exceeds threshold."""
    umi_counts = list(range(50000, 0, -1))
    result = QCDashboardService._build_barcode_rank_data(umi_counts, max_points=500)

    # Should be at most 500 points
    assert len(result) <= 500
    # First and last points preserved
    assert result[0][0] == 1
    assert result[0][1] == 50000
    assert result[-1][0] == 50000


def test_build_barcode_rank_data_empty():
    """Returns empty list for empty input."""
    result = QCDashboardService._build_barcode_rank_data([])
    assert result == []


def test_build_barcode_rank_data_small_input():
    """Returns all points when input is small."""
    umi_counts = [500, 300, 100]
    result = QCDashboardService._build_barcode_rank_data(umi_counts)
    assert len(result) == 3
    assert result == [[1, 500], [2, 300], [3, 100]]


# ---------------------------------------------------------------------------
# UMIperCellSorted.txt parsing
# ---------------------------------------------------------------------------


def test_read_umi_per_cell_sorted():
    """Parses UMIperCellSorted.txt into barcode rank data."""
    text = "57954\n53768\n44811\n36976\n31918\n29945\n25556\n"
    result = QCDashboardService._read_umi_per_cell_sorted(text)
    assert len(result) == 7
    assert result[0] == [1, 57954]
    assert result[-1] == [7, 25556]


def test_read_umi_per_cell_sorted_empty():
    """Returns empty list for empty input."""
    result = QCDashboardService._read_umi_per_cell_sorted("")
    assert result == []


# ---------------------------------------------------------------------------
# MultiQC chart data extraction (v2 format)
# ---------------------------------------------------------------------------

# MultiQC v2 uses cats[] for bar charts and lines[]/pairs for line charts
MULTIQC_V2_JSON = json.dumps(
    {
        "report_plot_data": {
            "star_alignment_plot": {
                "datasets": [
                    {
                        "cats": [
                            {"name": "Uniquely mapped", "data": [58311120], "data_pct": [87.55]},
                            {"name": "Mapped to multiple loci", "data": [5372117], "data_pct": [8.07]},
                            {"name": "Unmapped: too short", "data": [2712345], "data_pct": [4.07]},
                        ],
                        "samples": ["01"],
                    }
                ]
            },
            "fastqc_per_base_sequence_quality_plot": {
                "datasets": [
                    {
                        "lines": [
                            {"name": "01_1", "pairs": [[1, 36.0], [2, 36.1], [3, 36.2], [4, 36.0]]},
                            {"name": "01_2", "pairs": [[1, 35.7], [2, 35.4], [3, 35.5], [4, 35.5]]},
                        ]
                    }
                ]
            },
            "fastqc_per_sequence_gc_content_plot": {
                "datasets": [
                    {
                        "lines": [
                            {"name": "01_1", "pairs": [[0, 0.1], [20, 0.5], [40, 5.3], [60, 1.0], [100, 0.01]]},
                            {"name": "01_2", "pairs": [[0, 0.2], [20, 0.7], [40, 5.1], [60, 1.2], [100, 0.03]]},
                        ]
                    }
                ]
            },
            "fastqc_sequence_duplication_levels_plot": {
                "datasets": [
                    {
                        "lines": [
                            {"name": "01_1", "pairs": [[1, 24.8], [2, 23.0], [3, 20.5]]},
                            {"name": "01_2", "pairs": [[1, 25.2], [2, 22.6], [3, 19.5]]},
                        ]
                    }
                ]
            },
        }
    }
)


def test_read_multiqc_chart_data_v2_extracts_all_plots():
    """Extracts structured chart data from MultiQC v2 report_plot_data."""
    chart_data = QCDashboardService._read_multiqc_chart_data(MULTIQC_V2_JSON)

    assert "star_alignment" in chart_data
    assert "base_quality" in chart_data
    assert "gc_content" in chart_data
    assert "duplication" in chart_data


def test_read_multiqc_chart_data_v2_star_alignment():
    """STAR alignment uses cats with data_pct."""
    chart_data = QCDashboardService._read_multiqc_chart_data(MULTIQC_V2_JSON)
    star = chart_data["star_alignment"]

    assert isinstance(star, list)
    assert len(star) == 3
    assert star[0]["name"] == "Uniquely mapped"
    assert star[0]["value"] == 87.55


def test_read_multiqc_chart_data_v2_base_quality_averaged():
    """Base quality averages across samples from lines/pairs."""
    chart_data = QCDashboardService._read_multiqc_chart_data(MULTIQC_V2_JSON)
    bq = chart_data["base_quality"]

    assert isinstance(bq, list)
    assert len(bq) == 4
    # Average of 36.0 and 35.7
    assert bq[0][0] == 1
    assert bq[0][1] == pytest.approx(35.85, abs=0.01)


def test_read_multiqc_chart_data_v2_gc_content():
    """GC content averages across samples."""
    chart_data = QCDashboardService._read_multiqc_chart_data(MULTIQC_V2_JSON)
    gc = chart_data["gc_content"]

    assert "sample" in gc
    assert len(gc["sample"]) == 5
    # Average of 0.5 and 0.7 at GC%=20
    assert gc["sample"][1][0] == 20
    assert gc["sample"][1][1] == pytest.approx(0.6, abs=0.01)


def test_read_multiqc_chart_data_v2_duplication():
    """Duplication averages across samples."""
    chart_data = QCDashboardService._read_multiqc_chart_data(MULTIQC_V2_JSON)
    dup = chart_data["duplication"]

    assert isinstance(dup, list)
    assert len(dup) == 3
    # Average of 24.8 and 25.2
    assert dup[0][0] == 1
    assert dup[0][1] == pytest.approx(25.0, abs=0.01)


def test_read_multiqc_chart_data_empty_json():
    """Returns empty dict for JSON with no plot data."""
    chart_data = QCDashboardService._read_multiqc_chart_data("{}")
    assert chart_data == {}


def test_read_multiqc_chart_data_v2_partial():
    """Handles JSON with only some plots present."""
    partial = json.dumps(
        {
            "report_plot_data": {
                "star_alignment_plot": {
                    "datasets": [
                        {"cats": [{"name": "Uniquely mapped", "data": [100], "data_pct": [90.0]}], "samples": ["s1"]}
                    ]
                }
            }
        }
    )
    chart_data = QCDashboardService._read_multiqc_chart_data(partial)
    assert "star_alignment" in chart_data
    assert "base_quality" not in chart_data


def test_read_multiqc_chart_data_fallback_old_format():
    """Falls back to older data[].name/data format for STAR alignment."""
    old_format = json.dumps(
        {
            "report_plot_data": {
                "star_alignment_plot": {
                    "datasets": [
                        {
                            "data": [
                                {"name": "Uniquely mapped", "data": [{"x": "s1", "y": 85.5}]},
                                {"name": "Unmapped", "data": [{"x": "s1", "y": 14.5}]},
                            ]
                        }
                    ]
                }
            }
        }
    )
    chart_data = QCDashboardService._read_multiqc_chart_data(old_format)
    assert chart_data["star_alignment"][0]["name"] == "Uniquely mapped"
    assert chart_data["star_alignment"][0]["value"] == 85.5


def test_read_multiqc_chart_data_v2_string_x_values():
    """Handles string x-values in pairs by coercing to float."""
    mixed = json.dumps(
        {
            "report_plot_data": {
                "fastqc_per_base_sequence_quality_plot": {
                    "datasets": [
                        {
                            "lines": [
                                {"name": "s1", "pairs": [["1", 36.0], ["2", 35.5], ["3", 34.8]]},
                            ]
                        }
                    ]
                }
            }
        }
    )
    chart_data = QCDashboardService._read_multiqc_chart_data(mixed)
    bq = chart_data["base_quality"]
    assert len(bq) == 3
    assert bq[0][0] == 1.0
    assert bq[0][1] == 36.0
