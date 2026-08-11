"""Per-sample sequencing depth, derived from samples rather than files.

MultiQC's FastQC section has one entry per FILE, and a file is not a sample.
Three multipliers sit between them: mates (paired-end doubles the entries, each
reporting the same count), lanes (multiplies the entries, and the counts add up),
and samples. Counting entries conflates all three, which is why demo run 11 (one
sample across four files) reported either 133,203,774 or 33,300,944 depending on
which template read it, against a true depth of 66,601,887.

The derivation here:

1. Take the **sample roster** from an aligner section (STAR, samtools, ...),
   which is per-sample by construction because it is written after lanes are
   merged and mates paired.
2. Attribute each FastQC entry to its sample by name.
3. Within a sample, sum the DISTINCT per-file counts: mates report identical
   counts and collapse, lanes differ and add.

Depth deliberately still comes from FastQC, not from the aligner's own total,
because `total_sequences` is the RAW (pre-trim) count that papers report. STAR's
total_reads is post-trim: for run 17 it is 6,564,643 against a raw 6,677,908.
"""

import json
from pathlib import Path

from app.services.qc.multiqc_registry import (
    parse_multiqc_metrics,
    read_depth_and_samples,
    sample_roster,
)

FIXTURES = Path(__file__).parent / "fixtures" / "multiqc"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


# --------------------------------------------------------------------------
# The sample roster
# --------------------------------------------------------------------------


def test_roster_comes_from_the_aligner_not_from_fastqc_entries():
    """Run 11 is ONE sample written across four FastQC files."""
    data = _fixture("scrnaseq_run11.json")

    assert sample_roster(data["report_saved_raw_data"]) == ["SAMPLE-101"]
    assert len(data["report_saved_raw_data"]["multiqc_fastqc"]) == 4


def test_roster_counts_paired_end_samples_once():
    """Run 22 is two samples, each with an R1 and an R2 FastQC entry."""
    roster = sample_roster(_fixture("chipseq_run22.json")["report_saved_raw_data"])

    assert roster == ["SRX25642458_REP1_T1", "SRX25642461_REP1_T1"]


def test_roster_matches_fastqc_when_single_end_one_file_per_sample():
    roster = sample_roster(_fixture("bulk_rnaseq_run17.json")["report_saved_raw_data"])

    assert len(roster) == 4


def test_no_aligner_section_yields_no_roster():
    assert sample_roster({"multiqc_fastqc": {"s1": {"Total Sequences": 10.0}}}) == []


# --------------------------------------------------------------------------
# Depth
# --------------------------------------------------------------------------


def test_lanes_add_and_mates_collapse():
    """Run 11: one sample, two lanes, paired. 33,436,697 + 33,165,190, with each
    mate pair counted once. Matches STAR's own total_reads exactly."""
    depth, samples, _sources = read_depth_and_samples(_fixture("scrnaseq_run11.json"))

    assert depth == 66_601_887
    assert samples == 1


def test_paired_end_depth_is_unchanged_and_sample_count_is_corrected():
    """Run 22: the depth was already right (mates report identical counts, so
    averaging over files happened to be harmless); only the sample count was
    wrong."""
    depth, samples, _sources = read_depth_and_samples(_fixture("chipseq_run22.json"))

    assert depth == 24_427_238
    assert samples == 2


def test_single_end_runs_are_completely_unchanged():
    """Run 17: one file per sample, so there was never anything to correct. This
    is the regression guard for the common case."""
    depth, samples, _sources = read_depth_and_samples(_fixture("bulk_rnaseq_run17.json"))

    assert depth == 6_677_908
    assert samples == 4


def test_single_sample_paired_run_corrects_only_the_sample_count():
    depth, samples, _sources = read_depth_and_samples(_fixture("atacseq_run24.json"))

    assert depth == 58_365_790
    assert samples == 1


def test_depth_stays_the_raw_pre_trim_count():
    """The controlled key is the RAW read count, which is what papers report.
    The aligner's total is post-trim and must not silently replace it."""
    depth, _samples, _sources = read_depth_and_samples(_fixture("bulk_rnaseq_run17.json"))

    assert depth == 6_677_908
    assert depth != 6_564_643  # STAR's post-trim mean for the same run


def test_records_where_depth_and_sample_count_came_from():
    _depth, _samples, sources = read_depth_and_samples(_fixture("scrnaseq_run11.json"))

    assert sources["total_sequences"] == "fastqc"
    assert sources["total_samples"] == "star"


# --------------------------------------------------------------------------
# Degradation
# --------------------------------------------------------------------------


def test_without_a_roster_it_falls_back_to_the_per_file_mean():
    """No aligner section means no trustworthy grouping. Fall back to the old
    per-file behavior rather than guessing a grouping from FastQC names."""
    data = {
        "report_saved_raw_data": {
            "multiqc_fastqc": {
                "s1": {"Total Sequences": 1_000_000.0},
                "s2": {"Total Sequences": 3_000_000.0},
            }
        }
    }

    depth, samples, sources = read_depth_and_samples(data)

    assert depth == 2_000_000
    assert samples == 2
    assert sources["total_samples"] == "fastqc"


def test_a_fastqc_entry_matching_no_roster_sample_is_still_counted():
    """An unattributable entry must not vanish from the depth silently."""
    data = {
        "report_saved_raw_data": {
            "multiqc_star": {"sampleA": {"total_reads": 10.0}},
            "multiqc_fastqc": {
                "sampleA_1": {"Total Sequences": 1_000_000.0},
                "orphan_entry": {"Total Sequences": 5_000_000.0},
            },
        }
    }

    depth, samples, _sources = read_depth_and_samples(data)

    assert samples == 2
    assert depth == 3_000_000


def test_no_fastqc_at_all_yields_no_depth():
    data = {"report_saved_raw_data": {"multiqc_star": {"sampleA": {"total_reads": 10.0}}}}

    depth, samples, _sources = read_depth_and_samples(data)

    assert depth is None
    assert samples == 1


def test_identical_lane_counts_collapse_a_known_limitation():
    """Mates are recognized by reporting identical counts, so two lanes that
    happen to yield exactly the same number of reads are indistinguishable from
    a mate pair and get counted once. Documented rather than fixed: the
    alternative is trusting FastQC name suffixes, which vary by pipeline."""
    data = {
        "report_saved_raw_data": {
            "multiqc_star": {"s": {"total_reads": 10.0}},
            "multiqc_fastqc": {
                "s_L1": {"Total Sequences": 5_000_000.0},
                "s_L2": {"Total Sequences": 5_000_000.0},
            },
        }
    }

    depth, _samples, _sources = read_depth_and_samples(data)

    assert depth == 5_000_000


# --------------------------------------------------------------------------
# Wired into the generic engine
# --------------------------------------------------------------------------


def test_the_generic_engine_uses_the_corrected_derivation():
    metrics = parse_multiqc_metrics(_fixture("scrnaseq_run11.json"))

    assert metrics["total_sequences"] == 66_601_887
    assert metrics["total_samples"] == 1
