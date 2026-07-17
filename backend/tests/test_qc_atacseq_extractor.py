"""Pure-Python tests for the atacseq QC extractor (lit_validation Phase 4).

nf-core/atacseq is a sibling of chipseq (FastQC + samtools + Picard + MACS2 + FRiP)
minus antibody/immunoprecipitation, so there are no NSC/RSC; its distinctive signal
is chromatin accessibility (peaks + FRiP) and TSS enrichment. Aggregated per-sample
by MEAN. The ATAC-core section/key names are best-effort pending a real run, so the
parser is defensive and also scans report_general_stats_data -- both paths tested.
"""

import json

from app.services.qc.templates import atacseq

_MULTIQC = {
    "report_saved_raw_data": {
        "multiqc_fastqc": {  # raw
            "S1": {"Total Sequences": 20_000_000.0, "%GC": 45.0, "total_deduplicated_percentage": 40.0, "avg_sequence_length": 50.0},
            "S2": {"Total Sequences": 30_000_000.0, "%GC": 47.0, "total_deduplicated_percentage": 50.0, "avg_sequence_length": 50.0},
        },
        "multiqc_fastqc_1": {  # trimmed
            "S1": {"Total Sequences": 18_000_000.0, "%GC": 45.0},
            "S2": {"Total Sequences": 28_000_000.0, "%GC": 47.0},
        },
        "multiqc_samtools_flagstat": {
            "S1": {"mapped_passed_pct": 96.0},
            "S2": {"mapped_passed_pct": 90.0},
        },
        "multiqc_picard_dups": {
            "S1": {"PERCENT_DUPLICATION": 0.20},
            "S2": {"PERCENT_DUPLICATION": 0.30},
        },
        "multiqc_macs2_peak_count": {
            "S1": {"count": 60_000.0},
            "S2": {"count": 40_000.0},
        },
        "multiqc_frip_score": {
            "S1": {"frip": 0.25},
            "S2": {"frip": 0.15},
        },
        "multiqc_tss_enrichment": {
            "S1": {"tss_enrichment": 8.0},
            "S2": {"tss_enrichment": 6.0},
        },
    }
}


def test_read_multiqc_shared_metrics():
    m = atacseq.read_multiqc_metrics(json.dumps(_MULTIQC))
    assert m["total_sequences"] == 25_000_000
    assert m["total_samples"] == 2
    assert m["percent_gc"] == 46.0
    assert m["avg_sequence_length"] == 50.0
    assert m["percent_duplicates"] == 25.0  # Picard 0-1 -> percent, preferred
    assert m["reads_mapped_genome"] == 0.93


def test_read_multiqc_atac_core_from_named_sections():
    m = atacseq.read_multiqc_metrics(json.dumps(_MULTIQC))
    assert m["peak_count"] == 50_000  # mean(60k, 40k)
    assert m["frip"] == 0.2  # mean(0.25, 0.15)
    assert m["tss_enrichment"] == 7.0  # mean(8.0, 6.0)


def test_peak_and_frip_from_real_plot_sections():
    """nf-core MultiQC stores MACS2 peak count + FRiP as bar-plot data (multiqc_peak_count-plot /
    multiqc_frip_score-plot, {sample: {series: value}}), as verified on the real chipseq run."""
    fixture = {
        "report_saved_raw_data": {
            "multiqc_fastqc": _MULTIQC["report_saved_raw_data"]["multiqc_fastqc"],
            "multiqc_peak_count-plot": {"S1": {"S1": 60_000.0}, "S2": {"S2": 40_000.0}},
            "multiqc_frip_score-plot": {"S1": {"S1": 0.25}, "S2": {"S2": 0.15}},
        }
    }
    m = atacseq.read_multiqc_metrics(json.dumps(fixture))
    assert m["peak_count"] == 50_000
    assert m["frip"] == 0.2


def test_atac_core_from_general_stats_fallback():
    fixture = {
        "report_saved_raw_data": {"multiqc_fastqc": _MULTIQC["report_saved_raw_data"]["multiqc_fastqc"]},
        "report_general_stats_data": [
            {
                "S1": {"frip": 0.25, "peak_count": 60_000, "tss_enrichment": 8.0},
                "S2": {"frip": 0.15, "peak_count": 40_000, "tss_enrichment": 6.0},
            }
        ],
    }
    m = atacseq.read_multiqc_metrics(json.dumps(fixture))
    assert m["peak_count"] == 50_000
    assert m["frip"] == 0.2
    assert m["tss_enrichment"] == 7.0


def test_no_nsc_rsc_keys_for_atac():
    m = atacseq.read_multiqc_metrics(json.dumps(_MULTIQC))
    # ATAC has no strand-cross-correlation; those keys are not part of the ATAC template.
    assert "nsc" not in m
    assert "rsc" not in m


def test_missing_atac_sections_leave_honest_none():
    fastqc_only = {"report_saved_raw_data": {"multiqc_fastqc": _MULTIQC["report_saved_raw_data"]["multiqc_fastqc"]}}
    m = atacseq.read_multiqc_metrics(json.dumps(fastqc_only))
    assert m["total_sequences"] == 25_000_000
    assert m["peak_count"] is None
    assert m["frip"] is None
    assert m["tss_enrichment"] is None


def test_empty_on_garbage():
    assert atacseq.read_multiqc_metrics("not json {") == dict(atacseq.EMPTY_METRICS)


def test_generate_summary_describes_peaks_frip_tss():
    metrics = {
        "total_samples": 2,
        "total_sequences": 25_000_000,
        "reads_mapped_genome": 0.93,
        "peak_count": 50_000,
        "frip": 0.2,
        "tss_enrichment": 7.0,
        "quality_rating": "good",
    }
    summary = atacseq.generate_summary(metrics)
    assert "2 samples" in summary
    assert "50,000 accessible peaks" in summary
    assert "FRiP" in summary
    assert "TSS enrichment" in summary
    assert "Good" in summary


def test_compute_quality_flags_low_signal():
    assert atacseq.compute_quality({"peak_count": 0}) == "concerning"
    assert atacseq.compute_quality({"frip": 0.02, "peak_count": 100}) == "concerning"
    assert atacseq.compute_quality({"frip": 0.3, "tss_enrichment": 8.0, "reads_mapped_genome": 0.9, "peak_count": 50_000}) == "good"
    assert atacseq.compute_quality({}) == "pending_review"
