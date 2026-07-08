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
