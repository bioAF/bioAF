"""E2/E3/E4: the automatic classifier (lit_validation Phase 2).

Deterministic, auditable comparison of a paper's claimed QC metrics against the computed QC metrics,
followed by attribution and a classification. The LLM does NOT pick the verdict (spec-03): this is
pure rule-based code so a human can audit exactly why a study landed where it did.

Three layers:
- E2 (``compare_targets``): join each ComparisonTarget to a computed QC metric and rate it
  agree / diverge / not_reported / not_computed, tolerance-based (never exact match).
- E3 (``_attribute``): before a divergence becomes ``not_validated``, clear OUR side (was the pipeline
  a confident nf-core equivalent, was a recognized reference build used). If our side is plausibly
  responsible, the honest result is ``inconclusive``, not a strike against the paper.
- E4 (``classify_study``): the spec-03 decision over the comparisons + attribution.

The extractor emits free-form claim keys ("alignment_rate", "mean_raw_reads_per_sample") that do NOT
share the QC dashboard's controlled metric vocabulary (LEARNINGS "the important one for Phase 2"). This
module owns the deterministic normalization/mapping layer that bridges the two, plus percent<->fraction
unit reconciliation. A claim with no controlled counterpart is ``not_computed`` (surfaced, not hidden).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class MetricSpec:
    """A controlled QC metric key, its numeric scale, default tolerance, and paper-side synonyms."""

    key: str
    scale: str  # "fraction" (0-1) | "percent" (0-100) | "count"
    tolerance: float  # in the metric's own scale
    relative: bool  # True: tolerance is a fraction of the claimed value; False: absolute
    aliases: tuple[str, ...] = field(default_factory=tuple)


# The controlled vocabulary is the union of the QC templates' metric keys (bulk_rnaseq + scrnaseq;
# app/services/qc/templates/). Aliases are the free-form keys papers/extractors tend to use.
_SPECS: tuple[MetricSpec, ...] = (
    MetricSpec("reads_mapped_genome", "fraction", 0.05, False,
               ("alignment_rate", "mapping_rate", "mapped_reads", "percent_mapped", "overall_alignment_rate",
                "reads_mapped", "aligned_reads", "mapping_percentage", "star_alignment_rate", "alignment_percentage",
                "percent_aligned", "overall_mapping_rate")),
    MetricSpec("reads_mapped_genome_unique", "fraction", 0.05, False,
               ("unique_alignment_rate", "uniquely_mapped", "unique_mapping_rate", "uniquely_mapped_reads",
                "unique_reads", "uniquely_mapped_percent")),
    MetricSpec("total_sequences", "count", 0.25, True,
               ("total_reads", "raw_reads", "read_count", "sequencing_depth", "reads_per_sample",
                "mean_raw_reads_per_sample", "mean_reads_per_sample", "library_size", "number_of_read_pairs",
                "read_pairs", "total_read_pairs", "reads_generated",
                # `total_sequences` is the raw (pre-trim) read count; papers qualify it as "raw"/"pre-trim".
                # NB: post-trim read counts have no computed counterpart, so they stay unmapped on purpose.
                "mean_reads_per_sample_pre_trim", "reads_per_sample_pre_trim", "mean_reads_pre_trim",
                "raw_reads_per_sample", "mean_raw_reads")),
    MetricSpec("avg_sequence_length", "count", 0.10, True,
               ("read_length", "avg_read_length", "mean_read_length", "average_read_length")),
    MetricSpec("percent_duplicates", "percent", 5.0, False,
               ("duplication_rate", "percent_dups", "pct_duplicates", "dup_rate", "duplicate_rate", "duplication")),
    MetricSpec("percent_gc", "percent", 5.0, False,
               ("gc_content", "gc_percent", "pct_gc", "percent_gc_content", "gc")),
    MetricSpec("total_samples", "count", 0.0, False,
               ("n_samples", "num_samples", "sample_count", "number_of_samples")),
    MetricSpec("cell_count", "count", 0.25, True,
               ("n_cells", "num_cells", "estimated_cells", "recovered_cells", "number_of_cells", "cells_recovered",
                "estimated_number_of_cells", "cell_number", "cells")),
    MetricSpec("total_genes_detected", "count", 0.25, True, ("genes_detected", "total_genes")),
    MetricSpec("median_genes_per_cell", "count", 0.25, True, ("median_genes",)),
    MetricSpec("mean_genes_per_cell", "count", 0.25, True, ("mean_genes",)),
    MetricSpec("median_umi_per_cell", "count", 0.25, True, ("median_umi", "median_umis", "median_umis_per_cell")),
    MetricSpec("mean_umi_per_cell", "count", 0.25, True, ("mean_umi", "mean_umis", "mean_umis_per_cell")),
    MetricSpec("median_reads_per_cell", "count", 0.25, True, ("median_reads",)),
    MetricSpec("mean_reads_per_cell", "count", 0.25, True, ("mean_reads",)),
    MetricSpec("saturation", "fraction", 0.05, False, ("sequencing_saturation", "seq_saturation")),
    MetricSpec("valid_barcodes", "fraction", 0.05, False,
               ("valid_barcode_rate", "valid_bc", "fraction_valid_barcodes", "percent_valid_barcodes")),
    MetricSpec("mito_pct_median", "percent", 5.0, False,
               ("percent_mito", "mito_pct", "mitochondrial_pct", "pct_mito", "percent_mitochondrial")),
    # ChIP-seq (nf-core/chipseq; lit_validation Phase 4). Peaks + FRiP are what ChIP papers report;
    # NSC/RSC are phantompeakqualtools quality ratios rarely stated in prose (usually not_reported).
    # FRiP and peak counts are pipeline-parameter-sensitive, so the tolerances are deliberately soft
    # first-pass defaults (calibratable, like every tolerance here).
    MetricSpec("peak_count", "count", 0.25, True,
               ("peaks", "num_peaks", "n_peaks", "number_of_peaks", "peak_number", "called_peaks",
                "total_peaks", "macs2_peaks", "significant_peaks")),
    MetricSpec("frip", "fraction", 0.5, True,
               ("frip_score", "fraction_reads_in_peaks", "reads_in_peaks_fraction", "fraction_of_reads_in_peaks")),
    MetricSpec("nsc", "count", 0.15, False,
               ("normalized_strand_cross_correlation", "nsc_score", "normalized_scc")),
    MetricSpec("rsc", "count", 0.25, False,
               ("relative_strand_cross_correlation", "rsc_score", "relative_scc")),
    # ATAC-seq (nf-core/atacseq; lit_validation Phase 4). peak_count + frip are shared with ChIP;
    # TSS enrichment is ATAC's distinctive accessibility score (a unitless ratio, ~3-30). Soft default.
    MetricSpec("tss_enrichment", "count", 0.25, True,
               ("tss_score", "tsse", "tss_enrichment_score", "tss_enrichment_ratio")),
)

_CLEARED_MAPPING_CONFIDENCE = {"exact", "high", "full"}


def _slug(text) -> str:
    """Normalize a key or unit to a comparable slug: lowercased, non-alphanumerics collapsed to '_'."""
    s = re.sub(r"[^a-z0-9]+", "_", str(text or "").strip().lower())
    return s.strip("_")


_SPEC_BY_KEY: dict[str, MetricSpec] = {}
_KEY_LOOKUP: dict[str, str] = {}
for _spec in _SPECS:
    _SPEC_BY_KEY[_spec.key] = _spec
    _KEY_LOOKUP.setdefault(_slug(_spec.key), _spec.key)  # identity: a controlled key maps to itself
    for _alias in _spec.aliases:
        _KEY_LOOKUP.setdefault(_slug(_alias), _spec.key)


# The controlled QC vocabulary, exposed so the extractor prompt can steer claims toward keys E2 can
# actually join (reducing the not_computed coverage gap at the source).
CONTROLLED_METRIC_KEYS: tuple[str, ...] = tuple(_SPEC_BY_KEY)


def normalize_target_key(metric_key) -> str | None:
    """Map a paper-side claim key to a controlled QC metric key, or None if there is no counterpart."""
    return _KEY_LOOKUP.get(_slug(metric_key))


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _normalize_value(value: float, unit, spec: MetricSpec) -> float:
    """Reconcile a claimed value onto the computed metric's scale (percent <-> fraction)."""
    u = str(unit or "").strip().lower()
    if spec.scale == "fraction":
        # Computed metric is a 0-1 fraction; papers usually report a percentage.
        if "%" in u or "percent" in u or value > 1.5:
            return value / 100.0
    elif spec.scale == "percent":
        # Computed metric is 0-100; a paper may report a 0-1 fraction.
        if value <= 1.0 and "%" not in u and u in ("", "fraction", "ratio", "proportion"):
            return value * 100.0
    return value


def compare_targets(targets: list[dict], computed_metrics: dict | None) -> list[dict]:
    """E2: rate each target against the computed metric that shares its (normalized) key."""
    computed = computed_metrics or {}
    rows: list[dict] = []
    for t in targets or []:
        key = t.get("metric_key")
        claimed = t.get("claimed_value")
        unit = t.get("unit")
        tol = t.get("tolerance")
        mapped = normalize_target_key(key)
        row = {
            "metric_key": key,
            "mapped_key": mapped,
            "claimed_value": claimed,
            "claimed_normalized": None,
            "computed_value": None,
            "unit": unit,
            "delta": None,
            "within_tolerance": None,
            "verdict": None,
        }
        if not _is_number(claimed):
            row["verdict"] = "not_reported"
            rows.append(row)
            continue
        if mapped is None or not _is_number(computed.get(mapped)):
            row["verdict"] = "not_computed"
            rows.append(row)
            continue

        spec = _SPEC_BY_KEY[mapped]
        claimed_norm = _normalize_value(float(claimed), unit, spec)
        computed_val = float(computed[mapped])
        delta = computed_val - claimed_norm

        if _is_number(tol) and tol and tol > 0:
            # An explicit target tolerance is treated as a fraction of the claimed value.
            limit = abs(tol) * abs(claimed_norm) if claimed_norm else abs(tol)
        elif spec.relative:
            limit = spec.tolerance * abs(claimed_norm)
        else:
            limit = spec.tolerance
        within = abs(delta) <= limit

        row.update(
            claimed_normalized=claimed_norm,
            computed_value=computed_val,
            delta=delta,
            within_tolerance=bool(within),
            verdict="agree" if within else "diverge",
        )
        rows.append(row)
    return rows


def _attribute(mapping_confidence: str | None, reference_genome: str | None) -> dict:
    """E3: try to clear OUR side for a divergence. If we cannot, the divergence is unattributable."""
    reasons: list[str] = []
    cleared = True
    if (mapping_confidence or "").lower() not in _CLEARED_MAPPING_CONFIDENCE:
        cleared = False
        reasons.append(
            f"pipeline mapping confidence is '{mapping_confidence or 'unknown'}', so a pipeline/toolchain "
            "difference could explain the divergence"
        )
    if not reference_genome:
        cleared = False
        reasons.append("no recognized reference genome was used, so a reference-build mismatch cannot be ruled out")
    if cleared:
        reasons.append(
            "the pipeline is a confident nf-core equivalent and a recognized reference build was used, "
            "so our side does not explain the divergence"
        )
    return {"our_side": "cleared" if cleared else "suspected", "reasons": reasons}


def classify_study(
    targets: list[dict],
    computed_metrics: dict | None,
    *,
    mapping_confidence: str | None = None,
    reference_genome: str | None = None,
) -> dict:
    """E4: the spec-03 verdict over the E2 comparison + E3 attribution.

    Returns the per-metric comparisons, attribution, coverage counts, the classification, an
    ``auto_finalize`` flag (True only for a clean ``validated``), and human-readable reasoning. The
    caller (driver) auto-finalizes a clean validated study and holds everything else at ``comparing``
    with this as the suggested verdict for a human to ratify or override.
    """
    comparisons = compare_targets(targets, computed_metrics)
    comparable = [c for c in comparisons if c["verdict"] in ("agree", "diverge")]
    diverged = [c for c in comparable if c["verdict"] == "diverge"]
    coverage = {
        "targets": len(comparisons),
        "comparable": len(comparable),
        "agree": sum(1 for c in comparisons if c["verdict"] == "agree"),
        "diverge": len(diverged),
        "not_computed": sum(1 for c in comparisons if c["verdict"] == "not_computed"),
        "not_reported": sum(1 for c in comparisons if c["verdict"] == "not_reported"),
    }
    attribution = {"our_side": "n/a", "reasons": []}

    if not comparable:
        classification = "inconclusive"
        auto_finalize = False
        reasoning = (
            "The run completed, but none of the paper's claimed metrics could be compared to a computed "
            "QC metric (metric-key coverage gap), so agreement cannot be assessed. Needs a human."
        )
    elif not diverged:
        classification = "validated"
        # Auto-finalize (remove the human) only for SOLID agreement: several metrics agree, or we
        # compared every metric the paper claimed with no coverage gap. A lone agreeing metric amid
        # uncomparable claims is validated-but-thin -> suggest validated, but hold for a human. This is
        # the spike-00 "reproduced but few comparable metrics" caution made concrete.
        solid = len(comparable) >= 2 or coverage["not_computed"] == 0
        auto_finalize = solid
        if solid:
            reasoning = f"All {len(comparable)} comparable metric(s) agree with the paper within tolerance."
        else:
            reasoning = (
                f"The comparable metric agrees with the paper within tolerance, but "
                f"{coverage['not_computed']} other claimed metric(s) had no computed counterpart, so "
                "coverage is thin. Suggesting validated; confirm before finalizing."
            )
    else:
        attribution = _attribute(mapping_confidence, reference_genome)
        if attribution["our_side"] == "cleared":
            classification = "not_validated"
            reasoning = (
                f"{len(diverged)} metric(s) diverge beyond tolerance and our side was cleared "
                "(confident pipeline equivalent, recognized reference build), so the run did not reproduce "
                "the paper's values in our hands."
            )
        else:
            classification = "inconclusive"
            reasoning = (
                f"{len(diverged)} metric(s) diverge, but our side could not be cleared "
                f"({'; '.join(attribution['reasons'])}), so the divergence cannot be attributed to the paper."
            )
        auto_finalize = False

    return {
        "comparisons": comparisons,
        "attribution": attribution,
        "coverage": coverage,
        "classification": classification,
        "auto_finalize": auto_finalize,
        "reasoning": reasoning,
    }
