"""The generic MultiQC parser: module registry, normalization, aggregation.

One extractor that reads any nf-core run's multiqc_data.json and maps MultiQC
module sections onto the controlled QC vocabulary, so a pipeline type with no
tailored template still produces real metrics. Pure logic: no GCS, no DB.

Grounded in the real reports under tests/fixtures/multiqc/ (four MultiQC majors),
because every past QC bug here came from guessing the shape wrong.
"""

import json
import math
from pathlib import Path

from app.services.qc.multiqc_registry import (
    harvest_extras,
    normalize_section_id,
    parse_multiqc_metrics,
    read_general_stats,
    select_sections,
)

FIXTURES = Path(__file__).parent / "fixtures" / "multiqc"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


# --------------------------------------------------------------------------
# Section id normalization
# --------------------------------------------------------------------------


def test_strips_the_multiqc_prefix():
    assert normalize_section_id("multiqc_star") == "star"


def test_strips_stage_repeat_suffixes():
    """chipseq runs samtools at several filtering stages; MultiQC numbers them."""
    assert normalize_section_id("multiqc_samtools_flagstat_1") == "samtools_flagstat"
    assert normalize_section_id("multiqc_samtools_flagstat_2") == "samtools_flagstat"


def test_strips_picard_instance_suffixes():
    assert normalize_section_id("multiqc_picard-1_insertSize") == "picard_insertsize"
    assert normalize_section_id("multiqc_picard_insertSize") == "picard_insertsize"


def test_strips_library_level_infixes():
    """atacseq prefixes merged-library sections; chipseq does not."""
    assert normalize_section_id("multiqc_mlib_peak_count-plot") == "peak_count-plot"
    assert normalize_section_id("multiqc_peak_count-plot") == "peak_count-plot"
    assert normalize_section_id("multiqc_mrep_frip_score-plot") == "frip_score-plot"


def test_keeps_sections_without_the_prefix():
    """Not every section is prefixed (qualimap, preseq, deeptools)."""
    assert normalize_section_id("qualimap_rnaseq_genome_results") == "qualimap_rnaseq_genome_results"
    assert normalize_section_id("preseq") == "preseq"


# --------------------------------------------------------------------------
# Section selection: stage repeats must not be blended
# --------------------------------------------------------------------------


def test_stage_repeats_select_the_base_section_only():
    """multiqc_samtools_flagstat / _1 / _2 are the SAME tool at different
    filtering stages with genuinely different totals (real run-22: 47,634,700
    vs 33,270,464). Averaging across them would invent a number that describes
    no stage, so only the unsuffixed section is scored."""
    raw = _fixture("chipseq_run22.json")["report_saved_raw_data"]

    chosen = select_sections(raw, "samtools_flagstat")

    assert len(chosen) == 1
    totals = {s["flagstat_total"] for s in chosen[0].values()}
    # The pre-filter stage, for both samples.
    assert totals == {47_634_700, 50_050_040}
    # Not the post-filter stage, and not a blend of the two.
    assert 33_270_464 not in totals


def test_fastqc_selects_the_raw_section_by_read_count():
    """Read depth is a pre-trim quantity, and the raw section is not reliably
    the unsuffixed one. Trimming only removes reads, so raw is whichever
    section carries the higher total."""
    raw = _fixture("bulk_rnaseq_run17.json")["report_saved_raw_data"]

    chosen = select_sections(raw, "fastqc")

    assert len(chosen) == 1
    assert max(s["Total Sequences"] for s in chosen[0].values()) == 7_351_029


# --------------------------------------------------------------------------
# general_stats: the shape changed in MultiQC 1.31
# --------------------------------------------------------------------------


def test_reads_general_stats_list_shape_pre_131():
    """<= 1.23: a list of {sample: {column: value}}."""
    data = _fixture("bulk_rnaseq_run17.json")
    assert isinstance(data["report_general_stats_data"], list)

    values = read_general_stats(data, "total_sequences")

    assert values
    assert all(isinstance(v, float) for v in values)


def test_reads_general_stats_dict_shape_131_plus():
    """1.31+: a dict of {module: {sample: {column: value}}}. A parser that
    iterates this as a list walks the module NAMES and throws."""
    data = _fixture("scrnaseq_run11.json")
    assert isinstance(data["report_general_stats_data"], dict)

    values = read_general_stats(data, "total_sequences")

    assert values


def test_general_stats_tolerates_module_qualified_column_ids():
    """1.31 keys columns as `star-mapped_percent`; older reports use the bare
    column id. Both must resolve to the same metric."""
    data = _fixture("scrnaseq_run11.json")

    values = read_general_stats(data, "mapped_percent")

    assert values


# --------------------------------------------------------------------------
# Registry mapping against real reports
# --------------------------------------------------------------------------


def test_maps_fastqc_columns_to_controlled_keys():
    metrics = parse_multiqc_metrics(_fixture("bulk_rnaseq_run17.json"))

    # Per-sample MEAN, matching the established aggregation convention.
    assert metrics["total_sequences"] == 6_677_908
    assert metrics["total_samples"] == 4
    assert metrics["percent_gc"] == 41.2
    assert metrics["avg_sequence_length"] == 73.6


def test_converts_star_percentages_to_fractions():
    """The controlled vocabulary stores mapping rates as 0-1 fractions."""
    metrics = parse_multiqc_metrics(_fixture("bulk_rnaseq_run17.json"))

    assert metrics["reads_mapped_genome_unique"] == 0.7769


def test_maps_samtools_flagstat_mapped_percentage():
    """The generic path for any aligner-agnostic pipeline."""
    metrics = parse_multiqc_metrics(_fixture("chipseq_run22.json"))

    assert metrics["reads_mapped_genome"] == 0.9988


def test_star_mapping_works_on_both_multiqc_majors():
    """1.19 STAR reports uniquely_mapped_percent but no mapped_percent; 1.31
    reports both. A single exact-column mapping would cover only one."""
    old = parse_multiqc_metrics(_fixture("bulk_rnaseq_run17.json"))
    new = parse_multiqc_metrics(_fixture("scrnaseq_run11.json"))

    assert old["reads_mapped_genome_unique"] is not None
    assert new["reads_mapped_genome_unique"] is not None


def test_percent_duplication_fraction_is_scaled_to_percent():
    """Picard reports PERCENT_DUPLICATION as a FRACTION (0.207) despite the
    name; the controlled percent_duplicates key is 0-100. Preferring Picard over
    FastQC's sequence-duplication estimate reproduces the chipseq and atacseq
    templates' shipped values exactly (20.7 and 49.0)."""
    assert parse_multiqc_metrics(_fixture("chipseq_run22.json"))["percent_duplicates"] == 20.7
    assert parse_multiqc_metrics(_fixture("atacseq_run24.json"))["percent_duplicates"] == 49.0


def test_records_which_module_supplied_each_metric():
    """Two modules can claim the same key with different semantics (Picard's
    alignment duplication vs FastQC's sequence duplication). The number alone
    is not interpretable without knowing which one won."""
    metrics = parse_multiqc_metrics(_fixture("chipseq_run22.json"))

    assert metrics["metric_sources"]["percent_duplicates"] == "picard_dups"
    assert metrics["metric_sources"]["total_sequences"] == "fastqc"


def test_losing_source_for_a_contested_key_is_still_visible_in_extras():
    """Nothing is silently discarded: the runner-up keeps its module-qualified
    name so a scientist can see both numbers."""
    metrics = parse_multiqc_metrics(_fixture("chipseq_run22.json"))

    assert "fastqc.total_deduplicated_percentage" in metrics["additional_metrics"]


# --------------------------------------------------------------------------
# Conservative matching
# --------------------------------------------------------------------------


def test_near_miss_column_names_do_not_populate_a_controlled_key():
    """Exact column-id matching only. A substring match is how you silently map
    the wrong column and report a confidently wrong number."""
    data = {
        "report_saved_raw_data": {
            "multiqc_star": {"S1": {"uniquely_mapped_pct": 74.0}},
        }
    }

    metrics = parse_multiqc_metrics(data)

    assert metrics["reads_mapped_genome_unique"] is None
    assert metrics["additional_metrics"]["star.uniquely_mapped_pct"] == 74.0


def test_transcriptome_mapping_does_not_claim_the_genome_mapping_key():
    """salmon.percent_mapped is transcriptome pseudo-alignment, not genome
    mapping. Mapping it to reads_mapped_genome would compare a paper's genome
    alignment rate against a different quantity."""
    metrics = parse_multiqc_metrics(_fixture("bulk_rnaseq_run17.json"))

    assert metrics["metric_sources"].get("reads_mapped_genome") != "salmon"
    assert "salmon.percent_mapped" in metrics["additional_metrics"]


def test_non_finite_values_are_excluded():
    """Real samtools flagstat carries NaN for undefined percentages; json.loads
    yields float('nan'), which would poison a mean."""
    data = {
        "report_saved_raw_data": {
            "multiqc_samtools_flagstat": {
                "S1": {"mapped_passed_pct": float("nan")},
                "S2": {"mapped_passed_pct": 90.0},
            }
        }
    }

    metrics = parse_multiqc_metrics(data)

    assert metrics["reads_mapped_genome"] == 0.9
    assert not any(isinstance(v, float) and not math.isfinite(v) for v in metrics["additional_metrics"].values())


# --------------------------------------------------------------------------
# Extras harvesting and the width guard
# --------------------------------------------------------------------------


def test_unmapped_numeric_columns_land_in_extras_as_means():
    metrics = parse_multiqc_metrics(_fixture("bulk_rnaseq_run17.json"))

    assert "cutadapt.percent_trimmed" in metrics["additional_metrics"]


def test_distribution_sections_are_not_harvested():
    """preseq has 10,000 columns and dupradar 1,406: those are curves, not
    metric tables, and harvesting them would bury the real numbers."""
    metrics = parse_multiqc_metrics(_fixture("chipseq_run22.json"))

    assert not any(k.startswith("preseq.") for k in metrics["additional_metrics"])


def test_numeric_keyed_sections_are_not_harvested():
    """A section whose column keys are numbers is a histogram (junction
    saturation bins, coverage histograms), regardless of how many there are."""
    section = {"S1": {str(i): float(i) for i in range(10)}}

    assert harvest_extras({"multiqc_something": section}) == {}


def test_per_contig_sections_are_not_harvested():
    """samtools idxstats has one column per contig (196 in the real report)."""
    metrics = parse_multiqc_metrics(_fixture("bulk_rnaseq_run17.json"))

    assert not any(k.startswith("samtools_idxstats.") for k in metrics["additional_metrics"])


def test_extras_never_collide_with_controlled_keys():
    metrics = parse_multiqc_metrics(_fixture("chipseq_run22.json"))

    controlled = {k for k in metrics if k not in ("additional_metrics", "metric_sources")}
    assert not (controlled & set(metrics["additional_metrics"]))


def test_non_numeric_columns_are_excluded_from_extras():
    data = {
        "report_saved_raw_data": {
            "multiqc_cutadapt": {"S1": {"cutadapt_version": "4.6", "percent_trimmed": 2.0}},
        }
    }

    metrics = parse_multiqc_metrics(data)

    assert "cutadapt.cutadapt_version" not in metrics["additional_metrics"]
    assert metrics["additional_metrics"]["cutadapt.percent_trimmed"] == 2.0


# --------------------------------------------------------------------------
# Degradation
# --------------------------------------------------------------------------


def test_empty_report_yields_all_none_not_a_crash():
    metrics = parse_multiqc_metrics({})

    assert metrics["total_sequences"] is None
    assert metrics["additional_metrics"] == {}


def test_every_real_fixture_yields_at_least_one_controlled_metric():
    """The whole point: any nf-core report produces something real, including
    the MultiQC 1.31 report the scrnaseq parser returns all-null for."""
    for name in (
        "bulk_rnaseq_run17.json",
        "chipseq_run22.json",
        "atacseq_run24.json",
        "scrnaseq_run11.json",
    ):
        metrics = parse_multiqc_metrics(_fixture(name))
        populated = [
            k for k, v in metrics.items() if k not in ("additional_metrics", "metric_sources") and v is not None
        ]
        assert populated, f"{name} produced no controlled metrics"
