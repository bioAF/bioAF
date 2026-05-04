"""Pure-Python tests for scrnaseq template helpers (lifted from QCDashboardService).

These exercise the parsing helpers directly -- no GCS, no DB -- since the
extractor lift is supposed to be verbatim.
"""

from app.services.qc.templates import scrnaseq


def test_read_starsolo_summary_extracts_core_metrics():
    text = (
        "Estimated Number of Cells,5234\n"
        "Median Reads per Cell,12345\n"
        "Median Gene per Cell,1500\n"
        "Median UMI per Cell,4000\n"
        "Sequencing Saturation,0.81\n"
        "Number of Reads,150000000\n"
        "Reads With Valid Barcodes,0.97\n"
        "Q30 Bases in CB+UMI,0.95\n"
        "Q30 Bases in RNA read,0.93\n"
        "Reads Mapped to Genome: Unique+Multiple,0.92\n"
        "Reads Mapped to Genome: Unique,0.78\n"
        "Mean Reads per Cell,28000\n"
        "Mean UMI per Cell,5500\n"
        "Mean Gene per Cell,1700\n"
        "Total Gene Detected,18000\n"
        "UMIs in Cells,28000000\n"
    )
    metrics = scrnaseq.read_starsolo_summary(text)
    assert metrics["cell_count"] == 5234
    assert metrics["median_reads_per_cell"] == 12345
    assert metrics["saturation"] == 0.81
    assert metrics["valid_barcodes"] == 0.97
    assert metrics["reads_mapped_genome"] == 0.92
    assert metrics["total_genes_detected"] == 18000


def test_build_barcode_rank_data_passes_through_small_input():
    counts = [100, 50, 25, 10, 5]
    pairs = scrnaseq.build_barcode_rank_data(counts)
    assert pairs == [[1, 100], [2, 50], [3, 25], [4, 10], [5, 5]]


def test_build_barcode_rank_data_downsamples_large_input():
    counts = list(range(2000, 0, -1))
    pairs = scrnaseq.build_barcode_rank_data(counts, max_points=200)
    assert len(pairs) <= 200
    assert pairs[0] == [1, 2000]


def test_read_umi_per_cell_sorted_parses_descending_counts():
    text = "100\n50\n25\n10\n5\n\n"
    pairs = scrnaseq.read_umi_per_cell_sorted(text)
    assert pairs == [[1, 100], [2, 50], [3, 25], [4, 10], [5, 5]]


def test_read_multiqc_metrics_aggregates_across_samples():
    import json

    multiqc = {
        "report_general_stats_data": [
            {
                "sample_a": {"total_sequences": 1_000_000, "percent_duplicates": 20.0, "percent_gc": 50.0},
                "sample_b": {"total_sequences": 2_000_000, "percent_duplicates": 30.0, "percent_gc": 48.0},
            }
        ]
    }
    metrics = scrnaseq.read_multiqc_metrics(json.dumps(multiqc))
    assert metrics["total_sequences"] == 3_000_000
    assert metrics["total_samples"] == 2
    assert metrics["percent_duplicates"] == 25.0
    assert metrics["percent_gc"] == 49.0


def test_generate_summary_describes_cells_and_reads():
    metrics = {
        "cell_count": 5000,
        "median_genes_per_cell": 2000,
        "median_umi_per_cell": 8000,
        "saturation": 0.85,
        "quality_rating": "good",
    }
    summary = scrnaseq.generate_summary(metrics)
    assert "5,000 cells" in summary
    assert "Good" in summary
