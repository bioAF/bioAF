"""E2/E3/E4 classifier (lit_validation Phase 2).

Pure comparison + attribution + classification over an extracted set of ComparisonTargets and the
computed QC metrics. No DB, no LLM: the verdict is deterministic and auditable (spec-03).
"""

from app.services.validation_classifier_service import (
    CONTROLLED_METRIC_KEYS,
    CONTROLLED_METRIC_SPECS,
    attribute_divergences,
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
        rows = compare_targets(
            [_target("mean_reads_after_trimming_per_sample", 5_000_000)], {"total_sequences": 6_600_000}
        )
        assert rows[0]["verdict"] == "not_computed"

    def test_explicit_target_tolerance_overrides_the_default(self):
        # 5% claimed tolerance; computed is 10% off -> diverge even though the count default (25%) would agree.
        rows = compare_targets([_target("total_reads", 10_000_000, tolerance=0.05)], {"total_sequences": 9_000_000})
        assert rows[0]["verdict"] == "diverge"


class TestClassify:
    def test_all_agree_is_validated_and_auto_finalizes(self):
        # A finding (num_peaks -> peak_count) agrees alongside a QC floor (alignment rate); with a real
        # finding reproduced and no coverage gap, this is a clean validated that auto-finalizes.
        result = classify_study(
            [_target("num_peaks", 24_000), _target("alignment_rate", 83.4, unit="%")],
            {"peak_count": 25_000, "reads_mapped_genome": 0.834},
            mapping_confidence="exact",
            reference_genome="GRCh38",
        )
        assert result["classification"] == "validated"
        assert result["auto_finalize"] is True
        assert result["coverage"]["agree"] == 2
        assert result["coverage"]["finding_agree"] == 1

    def test_floor_only_agreement_is_inconclusive_not_validated(self):
        # spec-06 gate: the only comparable metrics are technical QC floors (read depth + mapping rate);
        # the paper's substantive claim has no computed counterpart. Reproducing to QC level is not
        # validating a finding -> inconclusive, with the scope stated in the reasoning.
        result = classify_study(
            [
                _target("total_reads", 7_000_000),
                _target("alignment_rate", 83.4, unit="%"),
                _target("differentially_expressed_genes", 1_200),
            ],
            {"total_sequences": 6_600_000, "reads_mapped_genome": 0.834},
            mapping_confidence="exact",
            reference_genome="GRCh38",
        )
        assert result["classification"] == "inconclusive"
        assert result["auto_finalize"] is False
        assert result["coverage"]["agree"] == 2
        assert result["coverage"]["finding_agree"] == 0
        assert "finding" in result["reasoning"].lower()

    def test_yield_metric_agreement_alone_is_inconclusive(self):
        # spec-06 refinement: cell yield + sequencing-depth metrics (cell_count, genes/UMI-per-cell)
        # are floors, not findings. Recovering a similar cell count is a yield floor, not a validated
        # finding; the real finding signal is Level-3 concordance. So an agreement on cell_count alone
        # no longer earns validated.
        result = classify_study(
            [_target("cell_count", 5_000)],
            {"cell_count": 5_100},
            mapping_confidence="exact",
            reference_genome="GRCh38",
        )
        assert result["classification"] == "inconclusive"
        assert result["coverage"]["agree"] == 1
        assert result["coverage"]["finding_agree"] == 0

    def test_genes_per_cell_agreement_alone_is_inconclusive(self):
        result = classify_study(
            [_target("median_genes_per_cell", 2_000)],
            {"median_genes_per_cell": 2_050},
            mapping_confidence="exact",
            reference_genome="GRCh38",
        )
        assert result["classification"] == "inconclusive"
        assert result["coverage"]["finding_agree"] == 0

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

    def test_thin_coverage_pass_with_a_finding_is_suggested_validated_but_held(self):
        # One FINDING maps + agrees (peak count), the other claim has no counterpart. A lone agreeing
        # finding is validated-but-thin: suggest validated, do NOT auto-finalize (hold for a human).
        result = classify_study(
            [_target("num_peaks", 24_000), _target("mean_reads_after_trimming_per_sample", 5e6)],
            {"peak_count": 25_000},
            mapping_confidence="partial",
        )
        assert result["classification"] == "validated"
        assert result["auto_finalize"] is False
        assert result["coverage"]["agree"] == 1
        assert result["coverage"]["finding_agree"] == 1
        assert result["coverage"]["not_computed"] == 1

    def test_thin_coverage_floor_only_is_inconclusive(self):
        # The old GSE309060 bulk smoke shape: read depth agrees, the other claim has no counterpart. Under
        # the spec-06 gate a lone QC-floor agreement no longer earns validated - it is inconclusive.
        result = classify_study(
            [_target("mean_raw_reads_per_sample", 7e6), _target("mean_reads_after_trimming_per_sample", 5e6)],
            {"total_sequences": 6_600_000},
            mapping_confidence="partial",
        )
        assert result["classification"] == "inconclusive"
        assert result["auto_finalize"] is False
        assert result["coverage"]["agree"] == 1
        assert result["coverage"]["finding_agree"] == 0
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

    def test_bare_peaks_prefix_does_not_over_strip_differential_subset_keys(self):
        # Regression (surfaced live on study 5): `peaks` is a direct alias but must NOT be a strip base -
        # as a prefix it would wrongly map differential-subset keys (peaks that GAINED/LOST accessibility)
        # to the total peak_count. Those are not total counts, so they stay unmapped.
        assert normalize_target_key("peaks_gained_accessibility") is None
        assert normalize_target_key("peaks_lost_accessibility") is None
        assert normalize_target_key("common_peaks_both_conditions") is None
        assert normalize_target_key("differentially_accessible_peaks_fdr05") is None
        # The bare alias itself still direct-maps (unchanged).
        assert normalize_target_key("peaks") == "peak_count"

    def test_real_study5_target_set_two_advisory_no_false_positive(self):
        # Faithful to study 5's persisted targets: only the two peak_count_* keys are advisory; the
        # differential-accessibility keys stay not_computed (no false positive), read depth agrees.
        # Under the spec-06 gate the study is INCONCLUSIVE: the only SCORED agreement is a QC floor
        # (reads_mapped_genome), and no finding was checkable (the peaks are advisory, not scored).
        targets = [
            _target("reads_mapped_genome", 96.0, unit="%"),
            _target("mito_pct_median", 1.2),
            _target("peak_count_quiescent", 74_834),
            _target("peak_count_activated", 90_000),
            _target("common_peaks_both_conditions", 40_000),
            _target("differentially_accessible_peaks_fdr05", 5_000),
            _target("peaks_gained_accessibility", 3_000),
            _target("peaks_lost_accessibility", 2_000),
            _target("differentially_expressed_genes", 1_200),
        ]
        result = classify_study(
            targets,
            {"reads_mapped_genome": 0.9978, "peak_count": 31_914},
            mapping_confidence="partial",
            reference_genome="GRCh38",
        )
        assert result["classification"] == "inconclusive"
        assert result["auto_finalize"] is False
        assert result["coverage"]["agree"] == 1
        assert result["coverage"]["finding_agree"] == 0
        assert result["coverage"]["diverge"] == 0
        assert result["coverage"]["advisory"] == 2
        # peaks_gained/lost must NOT be advisory - they stay not_computed alongside the rest.
        advisory_keys = {
            c["metric_key"] for c in result["comparisons"] if c["advisory"] and c["verdict"] in ("agree", "diverge")
        }
        assert advisory_keys == {"peak_count_quiescent", "peak_count_activated"}

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

    def test_advisory_peak_does_not_count_as_a_scored_divergence(self):
        # Study 5 shape: read-depth agrees (a QC floor), the paper's per-condition peak count is advisory
        # (would diverge vs per-sample), and the rest have no computed counterpart. The advisory row must
        # NOT be scored (diverge stays 0). The verdict is inconclusive because the only scored metric is a
        # QC floor (spec-06 gate), NOT because of the advisory basis mismatch.
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
        assert result["classification"] == "inconclusive"
        assert result["auto_finalize"] is False
        assert result["coverage"]["agree"] == 1
        assert result["coverage"]["diverge"] == 0
        assert result["coverage"]["advisory"] == 1
        assert result["coverage"]["finding_agree"] == 0
        assert result["coverage"]["not_computed"] == 1

    def test_advisory_divergence_never_becomes_not_validated_even_when_side_cleared(self):
        # Even with our side fully cleared (exact mapping + recognized genome), an advisory divergence
        # cannot strike the paper: only a SCORED divergence can reach not_validated. Here the only scored
        # metric is a QC floor, so the verdict is inconclusive - crucially, never not_validated.
        result = classify_study(
            [_target("total_reads", 7_000_000), _target("peak_count_activated", 90_000)],
            {"total_sequences": 6_600_000, "peak_count": 40_000},
            mapping_confidence="exact",
            reference_genome="GRCh38",
        )
        assert result["classification"] != "not_validated"
        assert result["classification"] == "inconclusive"
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


# ---- E3 per-metric divergence attribution + the veto it lifts (plan_0 step 5) ----

_AGREEING_CONCORDANCE = {
    "kind": "gene",
    "verdict": "agree",
    "paper_n": 100,
    "our_n": 95,
    "overlap": 88,
    "concordant": 85,
    "directional_overlap_frac": 0.85,
    "enrichment_p": 1e-30,
    "notes": [],
}


def _scrna_study(**kwargs):
    """A reproduced scRNA finding alongside the cell-count divergence STARsolo always produces."""
    defaults = dict(
        targets=[_target("cell_count", 10234)],
        computed_metrics={"cell_count": 7431},
        concordance_results=[_AGREEING_CONCORDANCE],
        paper_tools=["CellRanger", "Seurat"],
        pipeline_key="nf-core/scrnaseq",
    )
    defaults.update(kwargs)
    targets = defaults.pop("targets")
    computed = defaults.pop("computed_metrics")
    return classify_study(targets, computed, **defaults)


class TestToolAttribution:
    def test_cell_caller_difference_explains_the_yield_metrics(self):
        """bioAF runs STARsolo; most published scRNA papers used CellRanger. The two use different
        cell-calling algorithms and routinely disagree on cell count by more than the 25% tolerance,
        which cascades into genes/UMIs per cell and saturation."""
        attributed = attribute_divergences(
            [
                {"mapped_key": k, "claimed_normalized": 10.0, "computed_value": 5.0}
                for k in (
                    "cell_count",
                    "median_genes_per_cell",
                    "mean_genes_per_cell",
                    "median_umi_per_cell",
                    "mean_umi_per_cell",
                    "saturation",
                )
            ],
            paper_tools=["CellRanger"],
            pipeline_key="nf-core/scrnaseq",
        )
        assert set(attributed) == {
            "cell_count",
            "median_genes_per_cell",
            "mean_genes_per_cell",
            "median_umi_per_cell",
            "mean_umi_per_cell",
            "saturation",
        }
        assert attributed["cell_count"]["paper_tool"] == "CellRanger"
        assert attributed["cell_count"]["our_tool"] == "STARsolo"

    def test_a_cell_caller_difference_does_not_explain_the_mapping_rate(self):
        """Mapping rate is stable across pipelines. Explaining it away with a cell-caller difference
        would let a real divergence hide behind an unrelated cause."""
        attributed = attribute_divergences(
            [{"mapped_key": "reads_mapped_genome", "claimed_normalized": 0.95, "computed_value": 0.60}],
            paper_tools=["CellRanger"],
            pipeline_key="nf-core/scrnaseq",
        )
        assert attributed == {}

    def test_an_empty_tool_list_attributes_nothing(self):
        for tools in ([], None):
            assert (
                attribute_divergences(
                    [{"mapped_key": "cell_count", "claimed_normalized": 10.0, "computed_value": 5.0}],
                    paper_tools=tools,
                    pipeline_key="nf-core/scrnaseq",
                )
                == {}
            )

    def test_an_unrecognised_tool_list_attributes_nothing(self):
        assert (
            attribute_divergences(
                [{"mapped_key": "cell_count", "claimed_normalized": 10.0, "computed_value": 5.0}],
                paper_tools=["a bespoke in-house script"],
                pipeline_key="nf-core/scrnaseq",
            )
            == {}
        )

    def test_a_paper_that_used_our_own_tool_attributes_nothing(self):
        """If the paper called cells with STARsolo too, a cell-count divergence is NOT a tool
        difference and must keep counting against the verdict."""
        assert (
            attribute_divergences(
                [{"mapped_key": "cell_count", "claimed_normalized": 10.0, "computed_value": 5.0}],
                paper_tools=["STARsolo"],
                pipeline_key="nf-core/scrnaseq",
            )
            == {}
        )

    def test_attribution_is_deterministic(self):
        """spec-03: the LLM does not pick the verdict. The LLM's contribution is upstream (reading
        which tools the paper used); the attribution over that list is pure, auditable code."""
        args = ([{"mapped_key": "cell_count", "claimed_normalized": 10.0, "computed_value": 5.0}],)
        kwargs = {"paper_tools": ["CellRanger"], "pipeline_key": "nf-core/scrnaseq"}
        assert attribute_divergences(*args, **kwargs) == attribute_divergences(*args, **kwargs)


class TestAttributedDivergenceDoesNotVetoAFinding:
    def test_agreeing_concordance_plus_an_attributed_qc_divergence_validates(self):
        result = _scrna_study()
        assert result["classification"] == "validated"
        assert result["auto_finalize"] is False  # a consequential claim always holds for a human

    def test_the_reasoning_names_both_values_and_both_tools(self):
        """This sentence is the product; the verdict label is a summary of it."""
        reasoning = _scrna_study()["reasoning"]
        assert "10,234" in reasoning or "10234" in reasoning
        assert "7,431" in reasoning or "7431" in reasoning
        assert "CellRanger" in reasoning
        assert "STARsolo" in reasoning

    def test_an_unattributable_qc_divergence_still_vetoes(self):
        """An unexplained divergence must still count against the verdict; that is the whole reason
        not to make the gate simply tier-blind in the other direction."""
        result = _scrna_study(paper_tools=["a bespoke in-house script"])
        assert result["classification"] != "validated"

    def test_a_finding_tier_divergence_still_vetoes(self):
        """peak_count is finding-tier: the paper reporting a different number of peaks is a
        disagreement about a RESULT, not about data quality, whatever tool produced it."""
        result = classify_study(
            [_target("peak_count", 40000)],
            {"peak_count": 12000},
            concordance_results=[_AGREEING_CONCORDANCE],
            paper_tools=["MACS2", "HOMER"],
            pipeline_key="nf-core/chipseq",
        )
        assert result["classification"] != "validated"

    def test_a_concordance_divergence_still_vetoes(self):
        result = _scrna_study(concordance_results=[{**_AGREEING_CONCORDANCE, "verdict": "diverge", "concordant": 3}])
        assert result["classification"] != "validated"

    def test_no_finding_agreement_plus_a_qc_divergence_still_vetoes(self):
        """The lift only applies on top of a finding-tier agreement. Explaining away every QC
        divergence on a study that reproduced no finding would turn an honest inconclusive into a
        clean-looking one."""
        result = _scrna_study(concordance_results=None)
        assert result["classification"] != "validated"
        assert result["coverage"]["diverge"] == 1

    def test_the_attribution_is_reported_per_metric(self):
        result = _scrna_study()
        cause = result["divergence_attribution"]["cell_count"]
        assert cause["paper_tool"] == "CellRanger"
        assert cause["our_tool"] == "STARsolo"
        assert cause["cause"]

    def test_a_study_with_no_divergence_reports_no_attribution(self):
        result = classify_study(
            [_target("cell_count", 10234)],
            {"cell_count": 10000},
            concordance_results=[_AGREEING_CONCORDANCE],
            paper_tools=["CellRanger"],
            pipeline_key="nf-core/scrnaseq",
        )
        assert result["classification"] == "validated"
        assert result["divergence_attribution"] == {}


def test_every_controlled_metric_declares_what_it_means():
    """The spec block in the extraction prompt renders each metric's meaning, so a spec added
    without one reaches the model as a bare token, which is the defect the block exists to fix."""
    unexplained = [s.key for s in CONTROLLED_METRIC_SPECS if not s.meaning.strip()]
    assert unexplained == []


def test_controlled_metric_specs_are_the_controlled_vocabulary():
    """The exported specs and the exported keys must not drift apart."""
    assert tuple(s.key for s in CONTROLLED_METRIC_SPECS) == CONTROLLED_METRIC_KEYS


class TestBasisMismatchIsAdvisory:
    """A claim can name the right metric and still be measured on a different basis than bioAF's.

    The qualifier strip already catches the case where the basis is encoded in the KEY
    (`peak_count_quiescent`). It cannot catch the case where the key is exactly right and the basis
    is stated in the claim's own unit, which is what the extractor now produces: giving the model the
    specs (plan_6 step 1) made it emit the controlled key directly, so `peak_count` with unit
    "consensus peaks" bypassed the strip and was scored against a per-sample computed count.

    Measured live on 2026-09-02: study 5 emitted two consensus peak_count claims and study 3 emitted
    a before-trimming AND an after-trimming total_sequences claim, all four scored.
    """

    def test_a_consensus_peak_count_is_advisory(self):
        """bioAF computes per-sample MACS2 peaks. A consensus across replicates is a different
        number, and comparing the two rates a basis difference as if it were the paper's error."""
        rows = compare_targets(
            [_target("peak_count", 74_834, unit="consensus peaks")],
            {"peak_count": 31_914},
        )
        assert rows[0]["mapped_key"] == "peak_count"
        assert rows[0]["advisory"] is True
        assert "consensus" in (rows[0]["advisory_reason"] or "").lower()
        # Still surfaced with its numbers: advisory hides nothing, it only stops the scoring.
        assert rows[0]["computed_value"] == 31_914
        assert rows[0]["delta"] == 31_914 - 74_834

    def test_a_plain_peak_count_is_still_scored(self):
        """Study 20's shape. The claim states no conflicting basis, so nothing changes for it."""
        rows = compare_targets([_target("peak_count", 8_733, unit="peaks")], {"peak_count": 8_500})
        assert rows[0]["advisory"] is False
        assert rows[0]["advisory_reason"] is None
        assert rows[0]["verdict"] == "agree"

    def test_a_post_trim_read_count_is_advisory(self):
        """`total_sequences` is the RAW pre-trim count and the spec says so. Study 3's paper reported
        both counts and both were bound to it, so one was compared against a number it is not."""
        rows = compare_targets(
            [_target("total_sequences", 5_000_000, unit="reads per sample (approximate, after trimming)")],
            {"total_sequences": 7_000_000},
        )
        assert rows[0]["mapped_key"] == "total_sequences"
        assert rows[0]["advisory"] is True

    def test_a_pre_trim_read_count_is_scored(self):
        """ "before trimming" states the basis we compute on. It must not be caught by the same rule
        that catches "after trimming", which a bare "trim" substring would do."""
        rows = compare_targets(
            [_target("total_sequences", 7_000_000, unit="reads per sample (average, before trimming)")],
            {"total_sequences": 7_100_000},
        )
        assert rows[0]["advisory"] is False
        assert rows[0]["verdict"] == "agree"

    def test_the_basis_conflict_can_be_stated_in_the_key_instead_of_the_unit(self):
        """A model that keys the basis rather than unitting it must land in the same place."""
        rows = compare_targets(
            [_target("consensus_peak_count", 74_834, unit="peaks")],
            {"peak_count": 31_914},
        )
        assert rows[0]["mapped_key"] == "peak_count"
        assert rows[0]["advisory"] is True

    def test_a_basis_advisory_row_is_not_scored(self):
        """The whole point: a basis mismatch can neither strike the paper nor promote it. Study 5's
        live shape, with the consensus counts keyed exactly right."""
        result = classify_study(
            [
                _target("reads_mapped_genome", 96.0, unit="%"),
                _target("peak_count", 74_834, unit="consensus peaks"),
                _target("peak_count", 96_733, unit="consensus peaks"),
            ],
            {"reads_mapped_genome": 0.9978, "peak_count": 31_914},
            mapping_confidence="partial",
            reference_genome="GRCh38",
        )
        assert result["coverage"]["advisory"] == 2
        assert result["coverage"]["diverge"] == 0
        assert result["coverage"]["finding_agree"] == 0
        assert result["classification"] == "inconclusive"

    def test_the_reasoning_says_the_claims_were_measured_differently(self):
        """The advisory sentence used to name peak counts specifically, because the strip was the
        only path in. It has to cover a read count now too."""
        result = classify_study(
            [_target("total_sequences", 5_000_000, unit="reads after trimming")],
            {"total_sequences": 7_000_000},
            mapping_confidence="partial",
            reference_genome="GRCh38",
        )
        assert result["coverage"]["advisory"] == 1
        assert "advisory" in result["reasoning"].lower()
        assert "peak-count claim" not in result["reasoning"]


class TestComparisonHonoursTheRecordedBinding:
    """plan_6 step 3. The model's binding decision is stored on the target, and the comparison uses
    it. The alias table stays where it is and stops being the only path in."""

    def test_a_recorded_binding_wins_over_the_free_form_key(self):
        """Study 20's original shape: the paper's own key is `samd1_chip_peaks`, which resolves to
        nothing. With the binding recorded, the claim is compared against the computed peak count."""
        rows = compare_targets(
            [_target("samd1_chip_peaks", 8_733, unit="peaks") | {"bound_key": "peak_count"}],
            {"peak_count": 8_500},
        )
        assert rows[0]["mapped_key"] == "peak_count"
        assert rows[0]["verdict"] == "agree"
        assert rows[0]["advisory"] is False

    def test_a_target_with_no_recorded_binding_is_unchanged(self):
        """Every row written before this column existed keeps behaving exactly as it does now."""
        rows = compare_targets([_target("alignment_rate", 96.0, unit="%")], {"reads_mapped_genome": 0.9578})
        assert rows[0]["mapped_key"] == "reads_mapped_genome"
        assert rows[0]["verdict"] == "agree"

    def test_a_recorded_binding_outside_the_vocabulary_falls_back(self):
        """A stored key that is not controlled cannot be compared against anything, so the row must
        fall back to the alias table rather than claiming a mapping that does not exist."""
        rows = compare_targets(
            [_target("alignment_rate", 96.0, unit="%") | {"bound_key": "not_a_metric"}],
            {"reads_mapped_genome": 0.9578},
        )
        assert rows[0]["mapped_key"] == "reads_mapped_genome"

    def test_a_recorded_binding_is_still_subject_to_the_basis_rule(self):
        """The model binding a claim does not make its basis ours. Study 5's consensus counts stay
        advisory whether the key was resolved or recorded."""
        rows = compare_targets(
            [_target("total_peaks_in_study", 74_834, unit="consensus peaks") | {"bound_key": "peak_count"}],
            {"peak_count": 31_914},
        )
        assert rows[0]["mapped_key"] == "peak_count"
        assert rows[0]["advisory"] is True
