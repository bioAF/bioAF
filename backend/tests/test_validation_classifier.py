"""E2/E3/E4 classifier (lit_validation Phase 2).

Pure comparison + attribution + classification over an extracted set of ComparisonTargets and the
computed QC metrics. No DB, no LLM: the verdict is deterministic and auditable (spec-03).
"""

from app.services.validation_classifier_service import (
    classify_study,
    compare_targets,
    normalize_target_key,
)


def _target(metric_key, claimed_value=None, unit=None, tolerance=None):
    return {
        "metric_key": metric_key,
        "claimed_value": claimed_value,
        "unit": unit,
        "tolerance": tolerance,
        "source_locator": "Results",
    }


class TestKeyNormalization:
    def test_controlled_key_maps_to_itself(self):
        assert normalize_target_key("reads_mapped_genome") == "reads_mapped_genome"

    def test_synonyms_map_to_the_controlled_key(self):
        assert normalize_target_key("alignment_rate") == "reads_mapped_genome"
        assert normalize_target_key("Mapping Rate") == "reads_mapped_genome"
        assert normalize_target_key("total_reads") == "total_sequences"

    def test_the_mapping_layer_resolves_a_smoke_target_that_did_not_auto_join(self):
        # GSE309060's "raw reads per sample" claim has no LITERAL computed key, but it is semantically
        # the total sequenced reads -> the Phase 2 mapping layer bridges it (LEARNINGS Phase 2 point).
        assert normalize_target_key("mean_raw_reads_per_sample") == "total_sequences"

    def test_a_target_with_no_semantic_counterpart_stays_unmapped(self):
        # "reads after trimming" is a distinct quantity the QC dashboard does not compute; honestly unmapped.
        assert normalize_target_key("mean_reads_after_trimming_per_sample") is None

    def test_pre_trim_read_count_maps_but_post_trim_does_not(self):
        # From a real GSE309060 extraction: total_sequences is the raw (pre-trim) count, so the pre-trim
        # claim maps; the post-trim claim has no computed counterpart and must stay unmapped.
        assert normalize_target_key("mean_reads_per_sample_pre_trim") == "total_sequences"
        assert normalize_target_key("mean_reads_per_sample_post_trim") is None


class TestCompare:
    def test_agree_within_relative_tolerance_for_counts(self):
        rows = compare_targets([_target("total_reads", 7_000_000)], {"total_sequences": 6_600_000})
        assert rows[0]["verdict"] == "agree"
        assert rows[0]["mapped_key"] == "total_sequences"

    def test_diverge_outside_tolerance(self):
        rows = compare_targets([_target("cell_count", 10_000)], {"cell_count": 2_000})
        assert rows[0]["verdict"] == "diverge"

    def test_reconciles_percent_claim_against_a_fraction_metric(self):
        # Paper reports alignment as 83.4%; the QC metric is a 0-1 fraction. They agree.
        rows = compare_targets([_target("alignment_rate", 83.4, unit="%")], {"reads_mapped_genome": 0.834})
        assert rows[0]["verdict"] == "agree"

    def test_not_reported_when_the_paper_gave_no_value(self):
        rows = compare_targets([_target("total_reads", None)], {"total_sequences": 6_600_000})
        assert rows[0]["verdict"] == "not_reported"

    def test_not_computed_when_no_metric_shares_the_key(self):
        rows = compare_targets([_target("mean_reads_after_trimming_per_sample", 5_000_000)], {"total_sequences": 6_600_000})
        assert rows[0]["verdict"] == "not_computed"

    def test_explicit_target_tolerance_overrides_the_default(self):
        # 5% claimed tolerance; computed is 10% off -> diverge even though the count default (25%) would agree.
        rows = compare_targets([_target("total_reads", 10_000_000, tolerance=0.05)], {"total_sequences": 9_000_000})
        assert rows[0]["verdict"] == "diverge"


class TestClassify:
    def test_all_agree_is_validated_and_auto_finalizes(self):
        result = classify_study(
            [_target("total_reads", 7_000_000), _target("alignment_rate", 83.4, unit="%")],
            {"total_sequences": 6_600_000, "reads_mapped_genome": 0.834},
            mapping_confidence="exact",
            reference_genome="GRCh38",
        )
        assert result["classification"] == "validated"
        assert result["auto_finalize"] is True
        assert result["coverage"]["agree"] == 2

    def test_no_comparable_metric_is_inconclusive_not_validated(self):
        # When every claimed target maps to nothing the QC dashboard computes, we can assert neither
        # agreement NOR contradiction. Honest outcome is inconclusive, held for a human.
        result = classify_study(
            [_target("mean_reads_after_trimming_per_sample", 5e6), _target("de_log2fc_threshold", 1.0)],
            {"total_sequences": 6_600_000, "reads_mapped_genome": 0.834},
            mapping_confidence="partial",
        )
        assert result["classification"] == "inconclusive"
        assert result["auto_finalize"] is False
        assert result["coverage"]["not_computed"] == 2

    def test_thin_coverage_pass_is_suggested_validated_but_held_for_a_human(self):
        # The GSE309060 smoke: one metric maps + agrees (read depth), the other has no counterpart.
        # A lone agreeing metric is validated-but-thin: suggest validated, do NOT auto-finalize.
        result = classify_study(
            [_target("mean_raw_reads_per_sample", 7e6), _target("mean_reads_after_trimming_per_sample", 5e6)],
            {"total_sequences": 6_600_000},
            mapping_confidence="partial",
        )
        assert result["classification"] == "validated"
        assert result["auto_finalize"] is False
        assert result["coverage"]["agree"] == 1
        assert result["coverage"]["not_computed"] == 1

    def test_divergence_with_partial_mapping_is_inconclusive(self):
        # Our side is not cleared (partial pipeline substitution is a plausible cause), so a divergence
        # is inconclusive, never a strike against the paper.
        result = classify_study(
            [_target("cell_count", 10_000)],
            {"cell_count": 2_000},
            mapping_confidence="partial",
            reference_genome="GRCh38",
        )
        assert result["classification"] == "inconclusive"
        assert result["auto_finalize"] is False
        assert result["attribution"]["our_side"] == "suspected"

    def test_divergence_with_clean_reproduction_is_not_validated(self):
        # Exact pipeline match + a recognized reference build: our side is cleared, so a material
        # divergence is a defensible not_validated. Still held for a human (not auto-finalized).
        result = classify_study(
            [_target("cell_count", 10_000)],
            {"cell_count": 2_000},
            mapping_confidence="exact",
            reference_genome="GRCh38",
        )
        assert result["classification"] == "not_validated"
        assert result["auto_finalize"] is False
        assert result["attribution"]["our_side"] == "cleared"

    def test_reasoning_is_populated(self):
        result = classify_study([_target("total_reads", 7_000_000)], {"total_sequences": 6_600_000})
        assert isinstance(result["reasoning"], str) and result["reasoning"]


class TestChipSeqCoverage:
    """ChIP-seq controlled keys (lit_validation Phase 4): peak_count, frip, nsc, rsc."""

    def test_chip_aliases_map_to_controlled_keys(self):
        assert normalize_target_key("num_peaks") == "peak_count"
        assert normalize_target_key("Number of Peaks") == "peak_count"
        assert normalize_target_key("frip_score") == "frip"
        assert normalize_target_key("fraction_reads_in_peaks") == "frip"
        assert normalize_target_key("NSC") == "nsc"
        assert normalize_target_key("relative_strand_cross_correlation") == "rsc"

    def test_peak_count_agrees_within_relative_tolerance(self):
        rows = compare_targets([_target("num_peaks", 24_000)], {"peak_count": 25_000})
        assert rows[0]["verdict"] == "agree"
        assert rows[0]["mapped_key"] == "peak_count"

    def test_frip_percent_claim_reconciles_against_fraction_metric(self):
        # Paper reports FRiP as 4%; the computed metric is a 0-1 fraction. They agree.
        rows = compare_targets([_target("frip_score", 4.0, unit="%")], {"frip": 0.04})
        assert rows[0]["verdict"] == "agree"

    def test_chip_paper_all_agree_is_validated(self):
        result = classify_study(
            [_target("num_peaks", 24_000), _target("frip_score", 4.0, unit="%")],
            {"peak_count": 25_000, "frip": 0.04},
            mapping_confidence="exact",
            reference_genome="GRCh38",
        )
        assert result["classification"] == "validated"
        assert result["coverage"]["agree"] == 2


class TestAtacSeqCoverage:
    """ATAC-seq controlled keys (lit_validation Phase 4): peak_count + frip reuse ChIP; tss_enrichment new."""

    def test_tss_enrichment_aliases_map(self):
        assert normalize_target_key("tss_enrichment") == "tss_enrichment"
        assert normalize_target_key("TSS score") == "tss_enrichment"
        assert normalize_target_key("tsse") == "tss_enrichment"

    def test_tss_enrichment_agrees_within_tolerance(self):
        rows = compare_targets([_target("tss_score", 7.0)], {"tss_enrichment": 7.5})
        assert rows[0]["verdict"] == "agree"
        assert rows[0]["mapped_key"] == "tss_enrichment"

    def test_atac_paper_peaks_and_tss_validate(self):
        result = classify_study(
            [_target("num_peaks", 48_000), _target("tss_score", 7.0)],
            {"peak_count": 50_000, "tss_enrichment": 7.5},
            mapping_confidence="exact",
            reference_genome="GRCh38",
        )
        assert result["classification"] == "validated"
        assert result["coverage"]["agree"] == 2


class TestPeakCountQualifierAliasing:
    """Condition/consensus-qualified peak-count keys map to `peak_count` via a prefix-anchored strip
    (spec-05). The mapping is deliberately loose, so a stripped target is ADVISORY: surfaced with its
    number + delta, but never scored (papers report a consensus-across-replicates count; we compute a
    per-sample MACS2 count - a basis mismatch that must not drive a false verdict)."""

    def test_condition_qualified_peak_count_maps_to_peak_count(self):
        assert normalize_target_key("peak_count_quiescent") == "peak_count"
        assert normalize_target_key("peak_count_activated") == "peak_count"

    def test_qualified_alias_bases_also_strip_to_peak_count(self):
        # A qualifier hung off any listed peak alias, not just the canonical key, strips.
        assert normalize_target_key("num_peaks_consensus") == "peak_count"
        assert normalize_target_key("total_peaks_treated") == "peak_count"

    def test_non_base_peak_keys_stay_unmapped(self):
        # These do NOT start with a peak base token, so they are a different quantity (peaks that
        # CHANGED between conditions), not a total count. They must stay unmapped -> not_computed.
        assert normalize_target_key("differential_peaks") is None
        assert normalize_target_key("da_peaks") is None
        assert normalize_target_key("differentially_accessible_peaks") is None

    def test_bare_peak_count_and_direct_alias_unchanged(self):
        # A bare/direct claim still maps and is NOT advisory (preserves shipped ChIP/ATAC scoring).
        assert normalize_target_key("peak_count") == "peak_count"
        assert normalize_target_key("num_peaks") == "peak_count"

    def test_compare_flags_stripped_row_advisory_but_keeps_the_numbers(self):
        rows = compare_targets(
            [_target("peak_count_quiescent", 74_834), _target("num_peaks", 25_000)],
            {"peak_count": 31_914},
        )
        stripped, direct = rows[0], rows[1]
        # The stripped claim maps, is flagged advisory, and STILL carries the computed value + delta as
        # evidence the human reads (not hidden behind not_computed).
        assert stripped["mapped_key"] == "peak_count"
        assert stripped["advisory"] is True
        assert stripped["computed_value"] == 31_914
        assert stripped["delta"] == 31_914 - 74_834
        # The direct claim is a normal scored row.
        assert direct["advisory"] is False

    def test_advisory_row_does_not_regress_study5_from_validated(self):
        # Study 5 shape: read-depth agrees (scored), the paper's per-condition peak count is advisory
        # (would diverge vs per-sample), and the rest have no computed counterpart. The advisory
        # divergence must NOT count -> stays validated-thin, held for a human.
        result = classify_study(
            [
                _target("reads_mapped", 96.0, unit="%"),
                _target("peak_count_quiescent", 74_834),
                _target("differentially_accessible_peaks", 5_000),
            ],
            {"reads_mapped_genome": 0.9978, "peak_count": 31_914},
            mapping_confidence="partial",
            reference_genome="GRCh38",
        )
        assert result["classification"] == "validated"
        assert result["auto_finalize"] is False
        assert result["coverage"]["agree"] == 1
        assert result["coverage"]["diverge"] == 0
        assert result["coverage"]["advisory"] == 1
        assert result["coverage"]["not_computed"] == 1

    def test_advisory_divergence_never_becomes_not_validated_even_when_side_cleared(self):
        # Even with our side fully cleared (exact mapping + recognized genome), an advisory divergence
        # cannot strike the paper. Only a scored divergence can reach not_validated.
        result = classify_study(
            [_target("total_reads", 7_000_000), _target("peak_count_activated", 90_000)],
            {"total_sequences": 6_600_000, "peak_count": 40_000},
            mapping_confidence="exact",
            reference_genome="GRCh38",
        )
        assert result["classification"] == "validated"
        assert result["coverage"]["diverge"] == 0
        assert result["coverage"]["advisory"] == 1

    def test_only_advisory_claims_is_inconclusive_with_advisory_surfaced(self):
        # A paper whose ONLY mappable claims are per-condition peak counts has no scored metric, so the
        # honest outcome is inconclusive - but the surfaced peak numbers are still counted as advisory.
        result = classify_study(
            [_target("peak_count_quiescent", 74_834), _target("peak_count_activated", 90_000)],
            {"peak_count": 31_914},
            mapping_confidence="partial",
        )
        assert result["classification"] == "inconclusive"
        assert result["auto_finalize"] is False
        assert result["coverage"]["comparable"] == 0
        assert result["coverage"]["advisory"] == 2
