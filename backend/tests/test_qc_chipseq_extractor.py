"""Pure-Python tests for the chipseq QC extractor (lit_validation Phase 4).

The ChIP-seq template parses nf-core/chipseq MultiQC output and aggregates
per-sample by MEAN (the lit_validation comparison basis). A ChIP-seq run's
toolchain differs from bulk RNA-seq: alignment via samtools (not STAR),
duplication via Picard, plus the ChIP-specific NSC/RSC (phantompeakqualtools),
MACS2 peak count, and FRiP score.

These exercise the parser directly (no GCS, no DB) against a compact fixture
shaped like a real nf-core/chipseq multiqc_data.json. The ChIP-core section/key
names are best-effort pending a real run fixture, so the parser is defensive and
also scans report_general_stats_data as a fallback -- both paths are tested.
"""

import json

from app.services.qc.templates import chipseq

# Two samples. Raw FastQC carries >= reads than trimmed. samtools reports a
# 0-100 mapping percent; Picard reports a 0-1 duplication fraction; NSC/RSC,
# MACS2 peak count, and FRiP come from their own sections.
_MULTIQC = {
    "report_saved_raw_data": {
        "multiqc_fastqc": {  # raw (higher totals)
            "S1": {
                "Total Sequences": 20_000_000.0,
                "%GC": 45.0,
                "total_deduplicated_percentage": 40.0,  # -> 60% duplicates (FastQC)
                "avg_sequence_length": 50.0,
            },
            "S2": {
                "Total Sequences": 30_000_000.0,
                "%GC": 47.0,
                "total_deduplicated_percentage": 50.0,  # -> 50% duplicates (FastQC)
                "avg_sequence_length": 50.0,
            },
        },
        "multiqc_fastqc_1": {  # trimmed (lower totals)
            "S1": {"Total Sequences": 18_000_000.0, "%GC": 45.0, "avg_sequence_length": 49.0},
            "S2": {"Total Sequences": 28_000_000.0, "%GC": 47.0, "avg_sequence_length": 49.0},
        },
        "multiqc_samtools_flagstat": {
            "S1": {"mapped_passed_pct": 96.0, "mapped_passed": 19_200_000.0, "total_passed": 20_000_000.0},
            "S2": {"mapped_passed_pct": 90.0, "mapped_passed": 27_000_000.0, "total_passed": 30_000_000.0},
        },
        "multiqc_picard_dups": {
            "S1": {"PERCENT_DUPLICATION": 0.20},
            "S2": {"PERCENT_DUPLICATION": 0.30},
        },
        "multiqc_phantompeakqualtools": {
            "S1": {"NSC": 1.2, "RSC": 1.0},
            "S2": {"NSC": 1.4, "RSC": 1.2},
        },
        "multiqc_macs2_peak_count": {
            "S1": {"count": 30_000.0},
            "S2": {"count": 20_000.0},
        },
        "multiqc_frip_score": {
            "S1": {"frip": 0.05},
            "S2": {"frip": 0.03},
        },
    }
}


def test_read_multiqc_aggregates_fastqc_by_mean():
    m = chipseq.read_multiqc_metrics(json.dumps(_MULTIQC))
    assert m["total_sequences"] == 25_000_000  # mean(20M, 30M), from the RAW section
    assert m["total_samples"] == 2
    assert m["percent_gc"] == 46.0
    assert m["avg_sequence_length"] == 50.0


def test_picard_duplication_preferred_and_converted_from_fraction():
    m = chipseq.read_multiqc_metrics(json.dumps(_MULTIQC))
    # Picard PERCENT_DUPLICATION is 0-1; the vocab is 0-100. mean(0.20, 0.30) -> 25%.
    # This wins over the FastQC-derived duplication (which would be 55%).
    assert m["percent_duplicates"] == 25.0


def test_fastqc_duplication_used_when_no_picard():
    no_picard = {"report_saved_raw_data": dict(_MULTIQC["report_saved_raw_data"])}
    del no_picard["report_saved_raw_data"]["multiqc_picard_dups"]
    m = chipseq.read_multiqc_metrics(json.dumps(no_picard))
    # complement of deduplicated %: mean(100-40, 100-50) = mean(60, 50) = 55
    assert m["percent_duplicates"] == 55.0


def test_samtools_mapping_percent_to_fraction():
    m = chipseq.read_multiqc_metrics(json.dumps(_MULTIQC))
    # controlled vocab is a 0-1 fraction; samtools reports 0-100 percent. mean(0.96, 0.90).
    assert m["reads_mapped_genome"] == 0.93
    # samtools flagstat has no clean unique-mapping figure -> honest None.
    assert m["reads_mapped_genome_unique"] is None


def test_chip_core_nsc_rsc_peaks_frip_from_named_sections():
    m = chipseq.read_multiqc_metrics(json.dumps(_MULTIQC))
    assert m["nsc"] == 1.3  # mean(1.2, 1.4)
    assert m["rsc"] == 1.1  # mean(1.0, 1.2)
    assert m["peak_count"] == 25_000  # mean(30k, 20k)
    assert m["frip"] == 0.04  # mean(0.05, 0.03)


def test_peak_and_frip_from_real_plot_sections():
    """The REAL nf-core/chipseq MultiQC (verified against run-22 output) stores MACS2 peak count + FRiP
    as bar-plot data under multiqc_peak_count-plot / multiqc_frip_score-plot, shaped
    {sample: {series: value}} -- the inner key is the sample label, not a 'count'/'frip' column. Only
    the IP sample appears (the control has no peaks)."""
    fixture = {
        "report_saved_raw_data": {
            "multiqc_fastqc": _MULTIQC["report_saved_raw_data"]["multiqc_fastqc"],
            "multiqc_peak_count-plot": {"SRX_CHIP_REP1": {"SRX_CHIP_REP1": 16484.0}},
            "multiqc_frip_score-plot": {"SRX_CHIP_REP1": {"SRX_CHIP_REP1": 0.134985}},
        }
    }
    m = chipseq.read_multiqc_metrics(json.dumps(fixture))
    assert m["peak_count"] == 16484
    assert m["frip"] == 0.135


def test_chip_core_from_general_stats_fallback():
    """When the ChIP-core custom-content sections aren't under their own raw-data
    keys (they vary across chipseq versions), fall back to scanning the merged
    report_general_stats_data by fuzzy column name."""
    fixture = {
        "report_saved_raw_data": {
            "multiqc_fastqc": _MULTIQC["report_saved_raw_data"]["multiqc_fastqc"],
        },
        "report_general_stats_data": [
            {
                "S1": {"NSC": 1.2, "RSC": 1.0, "frip_score": 0.05, "peak_count": 30_000},
                "S2": {"NSC": 1.4, "RSC": 1.2, "frip_score": 0.03, "peak_count": 20_000},
            }
        ],
    }
    m = chipseq.read_multiqc_metrics(json.dumps(fixture))
    assert m["nsc"] == 1.3
    assert m["rsc"] == 1.1
    assert m["peak_count"] == 25_000
    assert m["frip"] == 0.04


def test_frip_percent_is_normalized_to_fraction():
    fixture = {
        "report_saved_raw_data": {
            "multiqc_frip_score": {"S1": {"frip": 4.0}, "S2": {"frip": 2.0}},  # reported as 0-100 percent
        }
    }
    m = chipseq.read_multiqc_metrics(json.dumps(fixture))
    assert m["frip"] == 0.03  # mean(0.04, 0.02)


def test_missing_chip_sections_leave_honest_none():
    """Depth is None here too, as of the no-aligner correction. FastQC alone has
    one entry per FILE, so with no aligner section and no emitted sheet to group
    by, a depth would be a per-file mean labelled per-sample. This test always
    said "honest none"; depth used to be the one exception."""
    fastqc_only = {"report_saved_raw_data": {"multiqc_fastqc": _MULTIQC["report_saved_raw_data"]["multiqc_fastqc"]}}
    m = chipseq.read_multiqc_metrics(json.dumps(fastqc_only))
    assert m["total_sequences"] is None
    assert m["peak_count"] is None
    assert m["frip"] is None
    assert m["nsc"] is None
    assert m["reads_mapped_genome"] is None


def test_empty_on_garbage():
    assert chipseq.read_multiqc_metrics("not json {") == dict(chipseq.EMPTY_METRICS)


def test_generate_summary_describes_peaks_and_frip():
    metrics = {
        "total_samples": 2,
        "total_sequences": 25_000_000,
        "reads_mapped_genome": 0.93,
        "peak_count": 25_000,
        "frip": 0.04,
        "nsc": 1.3,
        "rsc": 1.1,
        "quality_rating": "good",
    }
    summary = chipseq.generate_summary(metrics)
    assert "2 samples" in summary
    assert "25,000 peaks" in summary
    assert "FRiP" in summary
    assert "Good" in summary


def test_compute_quality_flags_no_peaks_as_concerning():
    assert chipseq.compute_quality({"peak_count": 0, "frip": 0.0}) == "concerning"
    assert chipseq.compute_quality({"frip": 0.05, "reads_mapped_genome": 0.9, "peak_count": 20_000}) == "good"
    assert chipseq.compute_quality({}) == "pending_review"
