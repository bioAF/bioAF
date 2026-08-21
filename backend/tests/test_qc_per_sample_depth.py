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
    roster_from_emitted,
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


def test_without_any_roster_neither_number_is_reported():
    """Changed deliberately. This asserted the old fallback: per-file mean as the
    depth and the ENTRY COUNT as the sample count, sourced "fastqc".

    That fallback is the file-for-sample confusion this module exists to prevent,
    and it reached users: every no-aligner run on the demo reported its FastQC
    file count as a sample count, so nf-core/demo runs of one sample over two
    lanes each said "4 samples". Two files might be one paired sample or two
    single-end samples, and nothing in the report says which, so the honest
    answer is neither number. A run launched by bioAF supplies the roster the
    report lacks (see below); one that predates that record gets no number
    rather than a wrong one."""
    data = {
        "report_saved_raw_data": {
            "multiqc_fastqc": {
                "s1": {"Total Sequences": 1_000_000.0},
                "s2": {"Total Sequences": 3_000_000.0},
            }
        }
    }

    depth, samples, sources = read_depth_and_samples(data)

    assert depth is None
    assert samples is None
    assert "total_samples" not in sources


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


# --------------------------------------------------------------------------
# When the pipeline runs no aligner at all
# --------------------------------------------------------------------------
#
# `#84` took the roster from an aligner section, which is correct by
# construction because it is written after lanes are merged and mates paired.
# A pipeline that runs no aligner has no such section, and that branch shipped
# reporting the FastQC ENTRY COUNT as the sample count and the per-file mean as
# the depth: exactly the file-for-sample confusion the rest of this file exists
# to prevent. Its own spec called for "FastQC grouped by sample; honest None if
# grouping is ambiguous."
#
# It is no longer ambiguous. bioAF now records the sheet each run submitted
# (`pipeline_runs.samplesheet_emitted_json`), so the roster can come from what
# bioAF ITSELF wrote rather than from guessing at FastQC's name suffixes.
#
# `generic_run34.json` is nf-core/demo's real report from demo run 34, MultiQC
# 1.33: `multiqc_fastqc` and `multiqc_general_stats` and nothing else. One
# sample over two lanes, paired, so four entries whose true depth is
# 33,436,697 + 33,165,190 = 66,601,887, the same library and the same ground
# truth as run 11.


def test_a_no_aligner_report_has_no_roster_of_its_own():
    data = _fixture("generic_run34.json")

    assert sample_roster(data["report_saved_raw_data"]) == []
    assert sorted(data["report_saved_raw_data"]["multiqc_fastqc"]) == [
        "SAMPLE-101_1",
        "SAMPLE-101_2",
        "SAMPLE-101_3",
        "SAMPLE-101_4",
    ]


def test_the_emitted_samplesheet_supplies_the_roster_a_no_aligner_run_lacks():
    """The whole point. Four files, one sample, and bioAF knows it because it
    wrote the sheet."""
    depth, samples, sources = read_depth_and_samples(_fixture("generic_run34.json"), emitted_roster=["SAMPLE-101"])

    assert samples == 1
    assert depth == 66_601_887
    assert sources["total_samples"] == "samplesheet"
    # Depth is still the RAW FastQC count. Only the ROSTER changed source.
    assert sources["total_sequences"] == "fastqc"


def test_without_any_roster_the_count_is_absent_rather_than_the_file_count():
    """What shipped reported 4 samples and a per-file mean for this run. Both
    were the file count wearing a sample's name. With nothing to group by, the
    honest answer is that we do not know."""
    depth, samples, sources = read_depth_and_samples(_fixture("generic_run34.json"))

    assert samples is None
    assert depth is None
    assert sources.get("total_samples") is None


def test_an_aligner_section_still_outranks_the_emitted_sheet():
    """The aligner's roster is written after lanes are merged and mates paired,
    so it stays authoritative where it exists. A sheet naming something else
    must not move a number that #84 already got right."""
    depth, samples, sources = read_depth_and_samples(_fixture("scrnaseq_run11.json"), emitted_roster=["SOMETHING-ELSE"])

    assert depth == 66_601_887
    assert samples == 1
    assert sources["total_samples"] == "star"


def test_an_emitted_roster_naming_nobody_in_the_report_is_not_a_roster():
    """A sheet whose names match no FastQC entry groups nothing. Reporting the
    sheet's length would be a sample count with no reads behind it."""
    depth, samples, _sources = read_depth_and_samples(
        _fixture("generic_run34.json"), emitted_roster=["UNRELATED-SAMPLE"]
    )

    assert samples is None
    assert depth is None


def test_roster_from_emitted_reads_the_names_bioaf_wrote():
    assert roster_from_emitted([{"name": "SAMPLE-101", "uuid": "abc", "sample_id": 1}]) == ["SAMPLE-101"]
    # Every shape a run that predates the column, or one that emitted nothing,
    # can present.
    assert roster_from_emitted(None) == []
    assert roster_from_emitted([]) == []
    assert roster_from_emitted([{"uuid": "abc"}]) == []
    assert roster_from_emitted("not a list") == []


def test_one_name_per_sample_however_many_rows_it_took():
    """A sample sequenced over two lanes emits two ROWS under one name. The
    roster is samples, so the name appears once."""
    assert roster_from_emitted(
        [
            {"name": "SAMPLE-101", "sample_id": 1},
            {"name": "SAMPLE-101", "sample_id": 1},
            {"name": "SAMPLE-102", "sample_id": 2},
        ]
    ) == ["SAMPLE-101", "SAMPLE-102"]


def test_the_generic_engine_carries_the_emitted_roster_through():
    metrics = parse_multiqc_metrics(_fixture("generic_run34.json"), emitted_roster=["SAMPLE-101"])

    assert metrics["total_samples"] == 1
    assert metrics["total_sequences"] == 66_601_887
    # The metrics that never depended on the roster are untouched.
    assert metrics["percent_gc"] == 46.0


def test_a_report_with_no_roster_reports_no_depth_either():
    """The count and the depth stand or fall together.

    The registry maps a file-level `total_sequences` out of general_stats before
    the per-sample derivation runs, and the derivation only OVERRIDES it when it
    has a number of its own. So suppressing the sample count alone left the
    per-file mean in place under the label "reads per sample", which is the same
    defect in the other sentence: run 34 would have said it had no sample count
    and a mean of 33,300,944 reads per sample.
    """
    metrics = parse_multiqc_metrics(_fixture("generic_run34.json"))

    assert metrics["total_samples"] is None
    assert metrics["total_sequences"] is None
    # Only the two that depend on knowing which files are one sample. Everything
    # else in the report is per-file by nature and stays.
    assert metrics["percent_gc"] == 46.0
    assert metrics["avg_sequence_length"] == 59.5
