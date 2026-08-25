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


# ---- nf-core/cutandrun (plan_1 step 3): the same peak QC under different section ids ----

# cutandrun reports its peak metrics to MultiQC as custom content, and the header files declare the
# section ids: `primary_peak_counts`, `consensus_peak_counts` and `primary_frip_score`
# (assets/multiqc/peak_counts_header.txt, peak_counts_consensus_header.txt, frip_score_header.txt
# @ 3.2.2). The raw-data key is that id plus `-plot` for a bargraph, which is the rule the real
# chipseq fixture proves: its header declares `#id: 'peak_count'` and the report carries
# `multiqc_peak_count-plot`. The values are per-sample, shaped {sample: {series: value}}.
#
# Derived, not observed: no real cutandrun report has been captured yet, so the parser accepts the
# bare id as well as the `-plot` form rather than pinning one exact key.
_CUTANDRUN_MULTIQC = {
    "report_saved_raw_data": {
        "multiqc_fastqc": {
            "S1": {"Total Sequences": 10_000_000.0, "%GC": 44.0, "avg_sequence_length": 50.0},
            "S2": {"Total Sequences": 12_000_000.0, "%GC": 45.0, "avg_sequence_length": 50.0},
        },
        "multiqc_samtools_flagstat": {
            "S1": {"mapped_passed_pct": 98.0},
            "S2": {"mapped_passed_pct": 94.0},
        },
        "multiqc_primary_peak_counts-plot": {
            "S1": {"S1": 12_000.0},
            "S2": {"S2": 8_000.0},
        },
        # The across-replicate figure. A different question from the per-sample count, and never
        # blended with it.
        "multiqc_consensus_peak_counts-plot": {
            "consensus": {"consensus": 6_000.0},
        },
        "multiqc_primary_frip_score-plot": {
            "S1": {"S1": 0.30},
            "S2": {"S2": 0.20},
        },
    },
    "report_general_stats_data": [],
}


def test_cutandrun_peak_count_is_the_per_sample_count():
    """A CUT&RUN paper's headline claim is a peak count, and peak_count is the one finding-tier
    scalar, so this single number is what lets a cutandrun study reach `validated` with no matrix
    and no notebook. Per-sample mean, the basis every other peak-calling assay is compared on."""
    metrics = chipseq.read_multiqc_metrics(json.dumps(_CUTANDRUN_MULTIQC))
    assert metrics["peak_count"] == 10_000  # mean(12000, 8000), not the 6000 consensus
    assert metrics["frip"] == 0.25


def test_cutandrun_falls_back_to_the_consensus_count_when_that_is_all_there_is():
    """A run configured to report only the consensus still yields a peak count rather than None.
    The number means something different, but it is a real number and the alternative is silence."""
    report = json.loads(json.dumps(_CUTANDRUN_MULTIQC))
    del report["report_saved_raw_data"]["multiqc_primary_peak_counts-plot"]
    metrics = chipseq.read_multiqc_metrics(json.dumps(report))
    assert metrics["peak_count"] == 6_000


def test_cutandrun_peak_sections_do_not_disturb_chipseq_parsing():
    """Regression: the added candidate ids must not change what a real nf-core/chipseq report
    yields."""
    metrics = chipseq.read_multiqc_metrics(json.dumps(_MULTIQC))
    assert metrics["peak_count"] == 25_000  # mean(30000, 20000) from multiqc_macs2_peak_count
