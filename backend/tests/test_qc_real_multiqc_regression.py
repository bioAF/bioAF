"""Regression lock: the shipped QC parsers, against real MultiQC reports.

The existing per-type extractors were debugged against live demo runs, and the
values they produce are what studies 3/4/5 were classified on. Before the
generic MultiQC engine lands beside them, pin those values to the actual reports
(`tests/fixtures/multiqc/`, see its README) so a refactor that changes a number
fails loudly instead of quietly re-rating a paper.

These assert the parsers' OUTPUT, not how they get there.
"""

import json
from pathlib import Path

import pytest

from app.services.qc.templates import atacseq, bulk_rnaseq, chipseq, scrnaseq

FIXTURES = Path(__file__).parent / "fixtures" / "multiqc"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_bulk_rnaseq_parses_real_run17_report():
    """nf-core/rnaseq 3.14 output, MultiQC 1.19: per-sample MEAN read depth."""
    metrics = bulk_rnaseq.read_multiqc_metrics(_fixture("bulk_rnaseq_run17.json"))

    assert metrics["total_samples"] == 4
    assert metrics["total_sequences"] == 6_677_908
    assert metrics["avg_sequence_length"] == 73.6
    assert metrics["percent_gc"] == 41.2
    assert metrics["percent_duplicates"] == 74.3
    assert metrics["reads_mapped_genome"] == 0.955
    assert metrics["reads_mapped_genome_unique"] == 0.7769


def test_chipseq_parses_real_run22_report():
    """nf-core/chipseq output, MultiQC 1.23. peak_count/FRiP come from bar-plot
    sections, NSC/RSC from phantompeakqualtools."""
    metrics = chipseq.read_multiqc_metrics(_fixture("chipseq_run22.json"))

    assert metrics["peak_count"] == 16_484
    assert metrics["frip"] == 0.135
    assert metrics["nsc"] == 1.044
    assert metrics["rsc"] == 0.222
    assert metrics["total_samples"] == 4
    assert metrics["total_sequences"] == 24_427_238
    assert metrics["reads_mapped_genome"] == 0.9988
    assert metrics["percent_duplicates"] == 20.7
    assert metrics["percent_gc"] == 43.0
    assert metrics["avg_sequence_length"] == 150.0
    # Unique mapping is not separable from samtools flagstat alone.
    assert metrics["reads_mapped_genome_unique"] is None


def test_atacseq_parses_real_run24_report():
    """nf-core/atacseq output, MultiQC 1.13. The peak sections carry the
    merged-library `mlib` infix here, unlike chipseq."""
    metrics = atacseq.read_multiqc_metrics(_fixture("atacseq_run24.json"))

    assert metrics["peak_count"] == 31_914
    assert metrics["frip"] == 0.0508
    assert metrics["total_samples"] == 2
    assert metrics["total_sequences"] == 58_365_790
    assert metrics["reads_mapped_genome"] == 0.9978
    assert metrics["percent_duplicates"] == 49.0
    assert metrics["percent_gc"] == 45.5
    assert metrics["avg_sequence_length"] == 150.0
    # nf-core/atacseq emits a deeptools TSS profile, not a scalar.
    assert metrics["tss_enrichment"] is None


@pytest.mark.xfail(
    reason=(
        "MultiQC 1.31 changed report_general_stats_data from a list of per-sample dicts to a dict "
        "keyed by module. The scrnaseq parser iterates it as a list, so on 1.31 output it hits "
        "'str' object has no attribute 'items', swallows the error, and returns all-null metrics. "
        "Pending owner decision (the generic engine handles both shapes)."
    ),
    strict=True,
)
def test_scrnaseq_parses_real_run11_report():
    """STAR + FastQC output, MultiQC 1.31."""
    metrics = scrnaseq.read_multiqc_metrics(_fixture("scrnaseq_run11.json"))

    assert metrics["total_sequences"] is not None
    assert metrics["percent_gc"] is not None


def test_scrnaseq_currently_returns_null_metrics_on_multiqc_131():
    """Characterization of the bug above: it degrades to all-null rather than
    raising, so a dashboard renders empty instead of erroring. Locked so the fix
    (whenever it is authorized) has to change this test deliberately."""
    metrics = scrnaseq.read_multiqc_metrics(_fixture("scrnaseq_run11.json"))

    assert metrics["total_sequences"] is None
    assert metrics["total_samples"] is None
    assert metrics["percent_gc"] is None
    assert metrics["avg_sequence_length"] is None
    assert metrics["percent_duplicates"] is None


def test_fixtures_span_multiple_multiqc_majors():
    """The fixture set exists to prove version drift is handled. If this ever
    collapses to one version, the drift coverage is gone."""
    versions = {
        json.loads(_fixture(name))["config_version"]
        for name in (
            "bulk_rnaseq_run17.json",
            "chipseq_run22.json",
            "atacseq_run24.json",
            "scrnaseq_run11.json",
        )
    }

    assert len(versions) >= 3
