"""Pure-Python tests for the bulk_rnaseq QC extractor.

The bulk template parses nf-core/rnaseq MultiQC output (report_saved_raw_data:
per-sample FastQC + STAR) and aggregates per-sample by MEAN, which is the E2
comparison basis a paper's per-sample read-depth claim is checked against
(lit_validation calibration, 2026-07-08). These exercise the parser directly --
no GCS, no DB -- against a compact fixture shaped like the real run-17 output.
"""

import json

from app.services.qc.templates import bulk_rnaseq

# Two samples. Raw FastQC always carries >= reads than trimmed (trimming only
# removes reads), so the raw section is the one with the higher read total and is
# what read depth / GC / length / duplication should be read from.
_MULTIQC = {
    "report_saved_raw_data": {
        "multiqc_fastqc": {  # raw (higher totals)
            "SAMP_A": {
                "Total Sequences": 6_000_000.0,
                "%GC": 40.0,
                "total_deduplicated_percentage": 30.0,  # -> 70% duplicates
                "avg_sequence_length": 75.0,
            },
            "SAMP_B": {
                "Total Sequences": 8_000_000.0,
                "%GC": 44.0,
                "total_deduplicated_percentage": 40.0,  # -> 60% duplicates
                "avg_sequence_length": 73.0,
            },
        },
        "multiqc_fastqc_1": {  # trimmed (lower totals)
            "SAMP_A": {
                "Total Sequences": 5_000_000.0,
                "%GC": 40.0,
                "total_deduplicated_percentage": 35.0,
                "avg_sequence_length": 74.0,
            },
            "SAMP_B": {
                "Total Sequences": 7_000_000.0,
                "%GC": 44.0,
                "total_deduplicated_percentage": 45.0,
                "avg_sequence_length": 72.0,
            },
        },
        "multiqc_star": {
            "SAMP_A": {
                "total_reads": 6_000_000.0,
                "uniquely_mapped": 4_800_000.0,
                "uniquely_mapped_percent": 80.0,
                "multimapped_percent": 15.0,
            },
            "SAMP_B": {
                "total_reads": 8_000_000.0,
                "uniquely_mapped": 5_600_000.0,
                "uniquely_mapped_percent": 70.0,
                "multimapped_percent": 20.0,
            },
        },
    }
}


def test_read_multiqc_metrics_aggregates_fastqc_by_mean():
    m = bulk_rnaseq.read_multiqc_metrics(json.dumps(_MULTIQC))
    # per-sample MEAN, not sum: (6M + 8M) / 2
    assert m["total_sequences"] == 7_000_000
    assert m["total_samples"] == 2
    assert m["percent_gc"] == 42.0
    # duplication = mean(100 - deduplicated_pct) = mean(70, 60)
    assert m["percent_duplicates"] == 65.0
    assert m["avg_sequence_length"] == 74.0


def test_read_multiqc_metrics_converts_star_percent_to_fraction():
    m = bulk_rnaseq.read_multiqc_metrics(json.dumps(_MULTIQC))
    # controlled vocab is a 0-1 fraction; STAR reports 0-100 percent
    # unique = mean(0.80, 0.70)
    assert m["reads_mapped_genome_unique"] == 0.75
    # total mapped = unique + multimapped = mean(0.95, 0.90)
    assert m["reads_mapped_genome"] == 0.925


def test_read_multiqc_metrics_prefers_raw_fastqc_regardless_of_suffix():
    """Raw is identified by having more reads (trimming only removes reads), not
    by the section name, so a run where the suffixed section is the raw one still
    reads depth from the raw section."""
    swapped = {
        "report_saved_raw_data": {
            # low totals under the un-suffixed key
            "multiqc_fastqc": {
                "S1": {"Total Sequences": 1_000_000.0, "%GC": 50.0},
                "S2": {"Total Sequences": 1_000_000.0, "%GC": 50.0},
            },
            # high totals under the suffixed key (the real raw section)
            "multiqc_fastqc_1": {
                "S1": {"Total Sequences": 9_000_000.0, "%GC": 42.0},
                "S2": {"Total Sequences": 11_000_000.0, "%GC": 42.0},
            },
        }
    }
    m = bulk_rnaseq.read_multiqc_metrics(json.dumps(swapped))
    assert m["total_sequences"] == 10_000_000  # mean of the high-total section
    assert m["percent_gc"] == 42.0


def test_read_multiqc_metrics_handles_missing_star_section():
    no_star = {"report_saved_raw_data": {"multiqc_fastqc": _MULTIQC["report_saved_raw_data"]["multiqc_fastqc"]}}
    m = bulk_rnaseq.read_multiqc_metrics(json.dumps(no_star))
    assert m["total_sequences"] == 7_000_000
    assert m["reads_mapped_genome"] is None
    assert m["reads_mapped_genome_unique"] is None


def test_read_multiqc_metrics_empty_on_garbage():
    m = bulk_rnaseq.read_multiqc_metrics("not json {")
    assert m == dict(bulk_rnaseq.EMPTY_METRICS)


def test_generate_summary_describes_reads_and_mapping():
    metrics = {
        "total_sequences": 7_000_000,
        "total_samples": 2,
        "reads_mapped_genome": 0.925,
        "percent_duplicates": 65.0,
        "quality_rating": "good",
    }
    summary = bulk_rnaseq.generate_summary(metrics)
    assert "2 samples" in summary
    assert "92" in summary  # mapping percentage surfaced
    assert "Good" in summary
