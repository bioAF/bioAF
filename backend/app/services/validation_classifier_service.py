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
    # One line of plain English, in the paper's terms rather than the pipeline's. It is what the
    # extraction prompt renders so the model can tell `peak_count` from `percent_gc` as concepts
    # instead of as tokens; a spec without one reaches the model meaning nothing.
    meaning: str = ""
    # "finding": a substantive result the paper reports (peaks, cells, genes/UMIs recovered).
    # "qc_floor": a technical data-quality/identity metric (mapping rate, read depth, GC, dup, ...).
    # A `validated` verdict requires at least one FINDING to agree - a floor-only agreement proves the
    # pipeline ran and the data is usable, not that any reported finding held up (spec-06). Default is
    # qc_floor so an unmarked metric can never earn validated on its own (conservative against overclaim).
    tier: str = "qc_floor"


# The controlled vocabulary is the union of the QC templates' metric keys (bulk_rnaseq + scrnaseq;
# app/services/qc/templates/). Aliases are the free-form keys papers/extractors tend to use.
_SPECS: tuple[MetricSpec, ...] = (
    MetricSpec(
        "reads_mapped_genome",
        "fraction",
        0.05,
        False,
        (
            "alignment_rate",
            "mapping_rate",
            "mapped_reads",
            "percent_mapped",
            "overall_alignment_rate",
            "reads_mapped",
            "aligned_reads",
            "mapping_percentage",
            "star_alignment_rate",
            "alignment_percentage",
            "percent_aligned",
            "overall_mapping_rate",
        ),
        meaning="share of sequenced reads that aligned anywhere on the reference genome",
    ),
    MetricSpec(
        "reads_mapped_genome_unique",
        "fraction",
        0.05,
        False,
        (
            "unique_alignment_rate",
            "uniquely_mapped",
            "unique_mapping_rate",
            "uniquely_mapped_reads",
            "unique_reads",
            "uniquely_mapped_percent",
        ),
        meaning="share of sequenced reads that aligned to exactly one genome location",
    ),
    MetricSpec(
        "total_sequences",
        "count",
        0.25,
        True,
        (
            "total_reads",
            "raw_reads",
            "read_count",
            "sequencing_depth",
            "reads_per_sample",
            "mean_raw_reads_per_sample",
            "mean_reads_per_sample",
            "library_size",
            "number_of_read_pairs",
            "read_pairs",
            "total_read_pairs",
            "reads_generated",
            # `total_sequences` is the raw (pre-trim) read count; papers qualify it as "raw"/"pre-trim".
            # NB: post-trim read counts have no computed counterpart, so they stay unmapped on purpose.
            "mean_reads_per_sample_pre_trim",
            "reads_per_sample_pre_trim",
            "mean_reads_pre_trim",
            "raw_reads_per_sample",
            "mean_raw_reads",
        ),
        meaning="raw reads (or read pairs) sequenced per sample, before trimming",
    ),
    MetricSpec(
        "avg_sequence_length",
        "count",
        0.10,
        True,
        ("read_length", "avg_read_length", "mean_read_length", "average_read_length"),
        meaning="mean read length in bases",
    ),
    MetricSpec(
        "percent_duplicates",
        "percent",
        5.0,
        False,
        ("duplication_rate", "percent_dups", "pct_duplicates", "dup_rate", "duplicate_rate", "duplication"),
        meaning="share of reads flagged as PCR or optical duplicates",
    ),
    MetricSpec(
        "percent_gc",
        "percent",
        5.0,
        False,
        ("gc_content", "gc_percent", "pct_gc", "percent_gc_content", "gc"),
        meaning="mean GC base content of the reads",
    ),
    MetricSpec(
        "total_samples",
        "count",
        0.0,
        False,
        ("n_samples", "num_samples", "sample_count", "number_of_samples"),
        meaning="how many samples the dataset contains",
    ),
    # scRNA cell yield + sequencing-depth metrics. spec-06 refinement (2026-07-25): these are QC-FLOOR
    # metrics (the default tier), NOT findings. Recovering a similar cell count / genes-per-cell / UMIs-
    # per-cell proves the data processed to a comparable yield and depth, not that any biological finding
    # (cell types, clusters, markers) reproduced. The real finding signal is Level-3 concordance (ADR-069),
    # so a yield/depth agreement on its own must not earn `validated`.
    MetricSpec(
        "cell_count",
        "count",
        0.25,
        True,
        (
            "n_cells",
            "num_cells",
            "estimated_cells",
            "recovered_cells",
            "number_of_cells",
            "cells_recovered",
            "estimated_number_of_cells",
            "cell_number",
            "cells",
        ),
        meaning="cells recovered after cell calling, per sample",
    ),
    MetricSpec(
        "total_genes_detected",
        "count",
        0.25,
        True,
        ("genes_detected", "total_genes"),
        meaning="distinct genes detected across the sample",
    ),
    MetricSpec(
        "median_genes_per_cell",
        "count",
        0.25,
        True,
        ("median_genes",),
        meaning="median number of genes detected in a single cell",
    ),
    MetricSpec(
        "mean_genes_per_cell",
        "count",
        0.25,
        True,
        ("mean_genes",),
        meaning="mean number of genes detected in a single cell",
    ),
    MetricSpec(
        "median_umi_per_cell",
        "count",
        0.25,
        True,
        ("median_umi", "median_umis", "median_umis_per_cell"),
        meaning="median UMI (transcript) count in a single cell",
    ),
    MetricSpec(
        "mean_umi_per_cell",
        "count",
        0.25,
        True,
        ("mean_umi", "mean_umis", "mean_umis_per_cell"),
        meaning="mean UMI (transcript) count in a single cell",
    ),
    MetricSpec(
        "median_reads_per_cell",
        "count",
        0.25,
        True,
        ("median_reads",),
        meaning="median sequencing reads assigned to a single cell",
    ),
    MetricSpec(
        "mean_reads_per_cell",
        "count",
        0.25,
        True,
        ("mean_reads",),
        meaning="mean sequencing reads assigned to a single cell",
    ),
    MetricSpec(
        "saturation",
        "fraction",
        0.05,
        False,
        ("sequencing_saturation", "seq_saturation"),
        meaning="sequencing saturation: how far the library was sequenced toward its complexity limit",
    ),
    MetricSpec(
        "valid_barcodes",
        "fraction",
        0.05,
        False,
        ("valid_barcode_rate", "valid_bc", "fraction_valid_barcodes", "percent_valid_barcodes"),
        meaning="share of reads carrying a barcode on the chemistry's whitelist",
    ),
    MetricSpec(
        "mito_pct_median",
        "percent",
        5.0,
        False,
        ("percent_mito", "mito_pct", "mitochondrial_pct", "pct_mito", "percent_mitochondrial"),
        meaning="median share of a cell's counts coming from mitochondrial genes",
    ),
    # ChIP-seq (nf-core/chipseq; lit_validation Phase 4). Peaks + FRiP are what ChIP papers report;
    # NSC/RSC are phantompeakqualtools quality ratios rarely stated in prose (usually not_reported).
    # FRiP and peak counts are pipeline-parameter-sensitive, so the tolerances are deliberately soft
    # first-pass defaults (calibratable, like every tolerance here).
    MetricSpec(
        "peak_count",
        "count",
        0.25,
        True,
        (
            "peaks",
            "num_peaks",
            "n_peaks",
            "number_of_peaks",
            "peak_number",
            "called_peaks",
            "total_peaks",
            "macs2_peaks",
            "significant_peaks",
        ),
        tier="finding",
        meaning="significant peaks called for the sample (the paper's headline peak number)",
    ),
    MetricSpec(
        "frip",
        "fraction",
        0.5,
        True,
        ("frip_score", "fraction_reads_in_peaks", "reads_in_peaks_fraction", "fraction_of_reads_in_peaks"),
        meaning="fraction of reads falling inside called peaks",
    ),
    MetricSpec(
        "nsc",
        "count",
        0.15,
        False,
        ("normalized_strand_cross_correlation", "nsc_score", "normalized_scc"),
        meaning="normalized strand cross-correlation, a ChIP enrichment quality ratio",
    ),
    MetricSpec(
        "rsc",
        "count",
        0.25,
        False,
        ("relative_strand_cross_correlation", "rsc_score", "relative_scc"),
        meaning="relative strand cross-correlation, a ChIP enrichment quality ratio",
    ),
    # ATAC-seq (nf-core/atacseq; lit_validation Phase 4). peak_count + frip are shared with ChIP;
    # TSS enrichment is ATAC's distinctive accessibility score (a unitless ratio, ~3-30). Soft default.
    MetricSpec(
        "tss_enrichment",
        "count",
        0.25,
        True,
        ("tss_score", "tsse", "tss_enrichment_score", "tss_enrichment_ratio"),
        meaning="accessibility signal at transcription start sites over background",
    ),
)

_CLEARED_MAPPING_CONFIDENCE = {"exact", "high", "full"}


# ---- E3 per-metric divergence attribution (plan_0 step 5) ----
#
# `_attribute` below asks "could OUR side explain this divergence?" with two inputs (mapping
# confidence, reference build), which is the right shape and too poor to answer the question, and it
# answers globally rather than per metric. This table answers it per metric from the one input that
# actually carries the information: which tools the PAPER said it used, next to which tools bioAF's
# pipeline actually runs.
#
# Curated and rule-based on purpose. spec-03 is explicit that the LLM does not pick the verdict, so a
# human can audit exactly why a study landed where it did. The LLM's contribution is upstream: reading
# the tool names out of the methods section.

# What bioAF's own pipelines use, per role. Read off the pipeline defaults in app/pipeline_defaults/:
# scrnaseq runs `aligner: star` (STARsolo), rnaseq runs `aligner: star_salmon` with
# `pseudo_aligner: salmon`, and atacseq/chipseq call peaks with MACS2 over BWA alignments.
_OUR_TOOLS: dict[str, dict[str, str]] = {
    "nf-core/scrnaseq": {"cell_caller": "STARsolo", "aligner": "STAR"},
    "nf-core/rnaseq": {"aligner": "STAR", "quantifier": "Salmon"},
    "nf-core/atacseq": {"aligner": "BWA", "peak_caller": "MACS2"},
    "nf-core/chipseq": {"aligner": "BWA", "peak_caller": "MACS2"},
}

# Paper-side tool names -> (role, canonical display name). A tool is listed under its DISTINGUISHING
# role: CellRanger aligns and quantifies too, but what separates it from STARsolo in practice is its
# cell-calling algorithm, and mapping rates are stable across both.
_TOOL_ROLES: tuple[tuple[str, str, str], ...] = (
    # cell calling (scRNA)
    ("cellranger", "cell_caller", "CellRanger"),
    ("cell ranger", "cell_caller", "CellRanger"),
    ("starsolo", "cell_caller", "STARsolo"),
    ("alevin", "cell_caller", "alevin-fry"),
    ("bustools", "cell_caller", "kallisto|bustools"),
    ("kallisto", "cell_caller", "kallisto|bustools"),
    ("dropseqtools", "cell_caller", "Drop-seq tools"),
    ("drop-seq", "cell_caller", "Drop-seq tools"),
    ("umi-tools", "cell_caller", "UMI-tools"),
    ("umi_tools", "cell_caller", "UMI-tools"),
    ("optimus", "cell_caller", "Optimus"),
    # expression quantification (bulk)
    ("salmon", "quantifier", "Salmon"),
    ("rsem", "quantifier", "RSEM"),
    ("kallisto", "quantifier", "kallisto"),
    ("featurecounts", "quantifier", "featureCounts"),
    ("htseq", "quantifier", "HTSeq"),
    ("cufflinks", "quantifier", "Cufflinks"),
    ("stringtie", "quantifier", "StringTie"),
    # genome alignment
    ("star", "aligner", "STAR"),
    ("hisat", "aligner", "HISAT2"),
    ("tophat", "aligner", "TopHat"),
    ("bowtie", "aligner", "Bowtie"),
    ("bwa", "aligner", "BWA"),
    ("subread", "aligner", "Subread"),
    # peak calling (ATAC/ChIP)
    ("macs2", "peak_caller", "MACS2"),
    ("macs3", "peak_caller", "MACS3"),
    ("macs", "peak_caller", "MACS"),
    ("homer", "peak_caller", "HOMER"),
    ("sicer", "peak_caller", "SICER"),
    ("epic2", "peak_caller", "epic2"),
    ("genrich", "peak_caller", "Genrich"),
)

# Which computed metrics a difference in each role can honestly explain. Deliberately narrow: a role
# that explains everything explains nothing, and a real divergence hiding behind an unrelated cause is
# worse than an unexplained one.
_ROLE_EXPLAINS: dict[str, frozenset[str]] = {
    "cell_caller": frozenset(
        {
            "cell_count",
            "median_genes_per_cell",
            "mean_genes_per_cell",
            "median_umi_per_cell",
            "mean_umi_per_cell",
            "median_reads_per_cell",
            "mean_reads_per_cell",
            "saturation",
            "valid_barcodes",
            "total_genes_detected",
            "mito_pct_median",
        }
    ),
    "quantifier": frozenset({"total_genes_detected"}),
    "aligner": frozenset({"reads_mapped_genome", "reads_mapped_genome_unique", "percent_duplicates"}),
    "peak_caller": frozenset({"peak_count", "frip"}),
}

_ROLE_CAUSE: dict[str, str] = {
    "cell_caller": "cell-calling algorithms differ",
    "quantifier": "expression quantifiers differ",
    "aligner": "genome aligners differ",
    "peak_caller": "peak callers differ",
}


def _fmt(value) -> str:
    """Format a metric value the way a scientist writes it: thousands separated, no trailing noise."""
    if not _is_number(value):
        return "not reported"
    v = float(value)
    if abs(v - round(v)) < 1e-9 and abs(v) >= 1:
        return f"{int(round(v)):,}"
    return f"{v:.4g}"


def _paper_tool_roles(paper_tools) -> dict[str, str]:
    """Map the paper's free-text tool names to {role: canonical name}. First match per role wins, so
    the order of ``_TOOL_ROLES`` is the precedence (a name matching two roles takes the earlier)."""
    found: dict[str, str] = {}
    for raw in paper_tools or []:
        text = _slug(raw).replace("_", "")
        for needle, role, display in _TOOL_ROLES:
            if _slug(needle).replace("_", "") in text:
                found.setdefault(role, display)
                break
    return found


def attribute_divergences(diverged: list[dict], *, paper_tools=None, pipeline_key=None) -> dict[str, dict]:
    """Explain each diverging metric by a NAMED difference between the paper's tool and ours.

    Returns ``{mapped_key: {"cause", "paper_tool", "our_tool", "explanation"}}`` for the divergences a
    known tool-pair difference accounts for, and says nothing about the rest. An unrecognised tool
    list, an empty one, or a paper that used the same tool we did all attribute nothing, so an
    unexplained divergence stays unexplained.
    """
    ours = _OUR_TOOLS.get(pipeline_key or "", {})
    theirs = _paper_tool_roles(paper_tools)
    if not ours or not theirs:
        return {}

    attributed: dict[str, dict] = {}
    for row in diverged or []:
        key = row.get("mapped_key")
        if not key:
            continue
        for role, our_tool in ours.items():
            paper_tool = theirs.get(role)
            if not paper_tool or _slug(paper_tool) == _slug(our_tool):
                continue
            if key not in _ROLE_EXPLAINS.get(role, frozenset()):
                continue
            cause = _ROLE_CAUSE[role]
            attributed[key] = {
                "cause": cause,
                "paper_tool": paper_tool,
                "our_tool": our_tool,
                "explanation": (
                    f"The paper reported {_fmt(row.get('claimed_normalized'))} for {key} using "
                    f"{paper_tool}; this run measured {_fmt(row.get('computed_value'))} using {our_tool}. "
                    f"{cause[0].upper()}{cause[1:]}, so this is an expected difference between the two "
                    "tools rather than a discrepancy in the paper's data."
                ),
            }
            break
    return attributed


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

# The same vocabulary with everything the model needs to choose between its members: what each metric
# means, the scale its number is on, whether it can earn a verdict, and the wordings papers use for it.
# The keys alone are bare tokens, and a bare token is not enough to bind a claim to (plan_6 step 1).
CONTROLLED_METRIC_SPECS: tuple[MetricSpec, ...] = _SPECS


# Peak-count claims are the one metric where papers routinely qualify the key by condition
# (`peak_count_quiescent`) or report a consensus-across-replicates figure, while we compute a
# per-sample MACS2 count. A prefix-anchored trailing-qualifier strip maps those to `peak_count` so the
# paper's number is surfaced next to the computed one. But the strip discards the qualifier that signals
# the basis may differ, so a target that mapped ONLY via the strip is ADVISORY (spec-05): rated and
# shown with its delta, but never scored, so a basis mismatch can never drive a false verdict. Scoped to
# peak_count deliberately - a general qualifier strip collides (e.g. `reads_mapped_genome_unique` would
# strip to the `reads_mapped` alias and mis-map to reads_mapped_genome). Longest base first so the most
# specific alias wins.
_PEAK_SPEC = _SPEC_BY_KEY["peak_count"]
# `peaks` (the bare plural) stays a direct alias but is NOT a strip base: as a prefix it over-matches
# differential-subset keys that are not total counts (`peaks_gained_accessibility`, `peaks_lost_*` - seen
# live on study 5). Every other peak alias carries a count/number/total/tool qualifier, so a
# `<base>_<qualifier>` match is unambiguously a peak COUNT.
_PEAK_STRIP_EXCLUDE = {_slug("peaks")}
_PEAK_STRIP_BASES: tuple[str, ...] = tuple(
    sorted(
        {_slug(_PEAK_SPEC.key), *(_slug(a) for a in _PEAK_SPEC.aliases)} - _PEAK_STRIP_EXCLUDE,
        key=len,
        reverse=True,
    )
)


def _resolve_key(metric_key) -> tuple[str | None, bool]:
    """Resolve a paper-side claim key to a controlled key, plus whether the match required stripping a
    trailing qualifier off a basis-sensitive peak base (which makes the target advisory)."""
    slug = _slug(metric_key)
    direct = _KEY_LOOKUP.get(slug)
    if direct is not None:
        return direct, False
    for base in _PEAK_STRIP_BASES:
        prefix = f"{base}_"
        if slug.startswith(prefix) and len(slug) > len(prefix):
            return "peak_count", True
    return None, False


def normalize_target_key(metric_key) -> str | None:
    """Map a paper-side claim key to a controlled QC metric key, or None if there is no counterpart."""
    return _resolve_key(metric_key)[0]


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _tier(mapped_key) -> str:
    """The metric's evidence tier: 'finding' (a substantive result) or 'qc_floor' (technical quality)."""
    spec = _SPEC_BY_KEY.get(mapped_key)
    return spec.tier if spec else "qc_floor"


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
        mapped, advisory = _resolve_key(key)
        row = {
            "metric_key": key,
            "mapped_key": mapped,
            "advisory": advisory,
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


def _attribute_differential(qc_attribution: dict, differential_attribution: dict) -> dict:
    """E3' (ADR-069): extend the our-side clearance to the differential step for a concordance
    divergence. A low concordance can strike the paper only if, ON TOP of the QC clearance, our
    reproduction applied the paper's stated thresholds and used a comparable DE/DA method. Any
    unmet differential clearance downgrades our side to 'suspected' (verdict -> inconclusive)."""
    diff_reasons: list[str] = []
    if not differential_attribution.get("thresholds_matched", False):
        diff_reasons.append(
            "our reproduction did not apply the paper's stated significance thresholds (|log2FC|/padj), "
            "so a threshold difference could explain the low overlap"
        )
    if not differential_attribution.get("method_comparable", False):
        diff_reasons.append(
            "the differential method was not established as comparable to the paper's, so a method "
            "difference could explain the low overlap"
        )
    if not diff_reasons:
        return qc_attribution  # QC clearance stands; the differential side is also cleared.
    # Our side is not cleared for the differential finding. Keep any QC suspicion, drop the QC
    # "cleared" rationale (it no longer holds), and record the differential reasons.
    qc_reasons = qc_attribution["reasons"] if qc_attribution["our_side"] == "suspected" else []
    return {"our_side": "suspected", "reasons": qc_reasons + diff_reasons}


def _concordance_desc(c: dict) -> str:
    frac = round(float(c.get("directional_overlap_frac", 0.0)) * 100)
    return (
        f"{c.get('concordant', 0)}/{c.get('paper_n', 0)} of the paper's {c.get('kind', '')} hits recovered "
        f"with concordant direction (directional overlap {frac}%, enrichment p={c.get('enrichment_p', 1.0):.1e})"
    )


def classify_study(
    targets: list[dict],
    computed_metrics: dict | None,
    *,
    mapping_confidence: str | None = None,
    reference_genome: str | None = None,
    concordance_results: list[dict] | None = None,
    differential_attribution: dict | None = None,
    paper_tools: list[str] | None = None,
    pipeline_key: str | None = None,
) -> dict:
    """E4: the spec-03 verdict over the E2 comparison + E3 attribution.

    Returns the per-metric comparisons, attribution, coverage counts, the classification, an
    ``auto_finalize`` flag (True only for a clean ``validated``), and human-readable reasoning. The
    caller (driver) auto-finalizes a clean validated study and holds everything else at ``comparing``
    with this as the suggested verdict for a human to ratify or override.

    ``concordance_results`` (E6, ADR-069) carries Level-3 finding-concordance verdicts (the paper's
    actual differential finding reproduced or not). A concordance ``agree`` is the strongest finding-tier
    agreement (it is the biological finding itself), so it satisfies the finding gate and can earn a
    Level-3 ``validated``; a concordance ``partial`` (real overlap enrichment, recovery below the agree
    line) is a strong-but-incomplete reproduction that earns ``partially_reproduced`` (held for a human)
    when nothing diverges and no full finding agrees; a concordance ``diverge`` is a divergence routed
    through the same attribution guard. When ``concordance_results`` is empty the logic reduces exactly
    to the Level-2 behavior.
    """
    comparisons = compare_targets(targets, computed_metrics)
    # Advisory rows (qualifier-stripped peak counts) are surfaced with their delta but never scored:
    # excluded from comparable/agree/diverge, so a basis mismatch can neither strike the paper nor
    # promote it to auto-finalize (spec-05). They are tallied separately as evidence for the human.
    comparable = [c for c in comparisons if c["verdict"] in ("agree", "diverge") and not c["advisory"]]
    diverged = [c for c in comparable if c["verdict"] == "diverge"]
    advisory = [c for c in comparisons if c["advisory"] and c["verdict"] in ("agree", "diverge")]
    # `validated` requires at least one FINDING to agree, not just a technical QC floor (spec-06).
    finding_agrees = [c for c in comparable if c["verdict"] == "agree" and _tier(c["mapped_key"]) == "finding"]

    # E6 Level-3 concordance verdicts (ADR-069). A concordance agree is a finding-tier agreement;
    # a concordance diverge is a divergence; a concordance partial is a strong-but-incomplete
    # reproduction (the overlap enrichment is real, but recovery is below the agree line). A partial
    # is neither a full finding agreement nor a divergence. not_computed concordance (e.g. namespace
    # mismatch) is unscored, like a not_computed metric.
    conc = concordance_results or []
    conc_agree = [c for c in conc if c.get("verdict") == "agree"]
    conc_diverge = [c for c in conc if c.get("verdict") == "diverge"]
    conc_partial = [c for c in conc if c.get("verdict") == "partial"]

    coverage = {
        "targets": len(comparisons),
        "comparable": len(comparable),
        "agree": sum(1 for c in comparable if c["verdict"] == "agree"),
        "diverge": len(diverged),
        "advisory": len(advisory),
        "finding_agree": len(finding_agrees),
        "not_computed": sum(1 for c in comparisons if c["verdict"] == "not_computed"),
        "not_reported": sum(1 for c in comparisons if c["verdict"] == "not_reported"),
        "concordance": len(conc),
        "concordance_agree": len(conc_agree),
        "concordance_partial": len(conc_partial),
        "concordance_diverge": len(conc_diverge),
    }
    attribution = {"our_side": "n/a", "reasons": []}

    # E3 per-metric attribution: which divergences a NAMED difference between the paper's tool and
    # ours accounts for. Pure and deterministic; the LLM's contribution was upstream, reading the tool
    # names out of the paper.
    divergence_attribution = attribute_divergences(diverged, paper_tools=paper_tools, pipeline_key=pipeline_key)

    # Combined predicates: a concordance verdict joins the scalar sets in the same decision tree.
    scored_any = bool(comparable) or bool(conc_agree) or bool(conc_diverge) or bool(conc_partial)
    has_finding = bool(finding_agrees) or bool(conc_agree)

    # An EXPLAINED qc_floor divergence no longer vetoes a finding-tier agreement. bioAF runs STARsolo
    # and most scRNA-seq papers used CellRanger, so a cell-count divergence beyond tolerance is the
    # expected case, not the edge case, and letting it silently veto a finding that reproduced cleanly
    # reports a known technical difference as if it were a discrepancy in the paper's data.
    #
    # Deliberately narrow. The lift needs a finding-tier agreement to sit on top of, every divergence
    # must be qc_floor, and every one must have an attributed cause. A finding-tier divergence, a
    # concordance `diverge`, an unattributable divergence, or no finding agreement at all all keep
    # today's behavior exactly: an unexplained divergence must still count against the verdict.
    explained_divergences = [
        c for c in diverged if _tier(c["mapped_key"]) == "qc_floor" and c["mapped_key"] in divergence_attribution
    ]
    veto_lifted = has_finding and not conc_diverge and bool(diverged) and len(explained_divergences) == len(diverged)
    has_diverge = (bool(diverged) or bool(conc_diverge)) and not veto_lifted
    # A partial finding reproduction is its own outcome: not a full agreement (so it does not satisfy
    # the finding gate), not a divergence (the overlap is real). It only decides the verdict when there
    # is no divergence and no full finding agreement to take precedence.
    has_partial = bool(conc_partial)

    if not scored_any:
        classification = "inconclusive"
        auto_finalize = False
        reasoning = (
            "The run completed, but none of the paper's claimed metrics could be compared to a computed "
            "QC metric (metric-key coverage gap), so agreement cannot be assessed. Needs a human."
        )
    elif not has_diverge and not has_finding and not has_partial:
        # A+B gate: every comparable metric agrees, but they are all technical QC floors (data
        # quality/identity), not the paper's findings. Reproducing to QC level is not validating a
        # finding, so the honest verdict is inconclusive with the scope stated plainly.
        classification = "inconclusive"
        auto_finalize = False
        reasoning = (
            f"{len(comparable)} technical QC metric(s) agree with the paper within tolerance, but those "
            "are data-quality floors (mapping rate, read depth, GC, ...), not the paper's findings. "
            f"None of the paper's finding-level claims were computable ({coverage['not_computed']} claim(s) "
            "had no computed counterpart; their differential/downstream analyses are outside the "
            "pipeline's scope). The deposited data is present and reproduces to QC level, but no reported "
            "finding was validated. Honest verdict: inconclusive, needs a human."
        )
    elif not has_diverge and conc_agree and not finding_agrees:
        # Level-3 validated driven purely by finding concordance (no scalar finding agreed). This is
        # the feature's whole point (the paper's reported finding reproduced), but it is a consequential
        # claim and thresholds are first-pass, so it is held for a human rather than auto-finalized.
        classification = "validated"
        auto_finalize = False
        reasoning = (
            f"The paper's reported finding reproduced: {'; '.join(_concordance_desc(c) for c in conc_agree)}. "
            "This is a Level-3 finding-level agreement. Suggesting validated; confirm before finalizing."
        )
    elif not has_diverge and has_finding:
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
        if conc_agree:
            reasoning += f" The paper's reported finding also reproduced ({_concordance_desc(conc_agree[0])})."
    elif not has_diverge and has_partial:
        # Strong-but-partial: the paper's finding partially reproduced. The overlap enrichment is
        # statistically real (not coincidence), but directional recovery is below the agree line, so
        # this is neither a full validation nor an unattributable divergence. It ALWAYS holds for a
        # human: it is inherently a "look at this" signal (never auto-finalizes).
        classification = "partially_reproduced"
        auto_finalize = False
        reasoning = (
            "The paper's reported finding PARTIALLY reproduced: "
            f"{'; '.join(_concordance_desc(c) for c in conc_partial)}. The overlap is statistically "
            "real (not coincidence), but recovery is below the agreement threshold, so part of the "
            "finding reproduced and part did not. Suggesting partially reproduced; needs a human."
        )
    else:
        attribution = _attribute(mapping_confidence, reference_genome)
        # E3' (ADR-069): a concordance divergence carries extra our-side risk beyond the QC guard.
        # Before it can strike the paper, the DIFFERENTIAL step must also be cleared: our reproduction
        # applied the paper's stated thresholds and used a comparable DE/DA method. If the caller
        # supplied that signal and it is not cleared, the divergence is unattributable -> inconclusive.
        if conc_diverge and differential_attribution is not None:
            attribution = _attribute_differential(attribution, differential_attribution)
        n_div = len(diverged) + len(conc_diverge)
        finding_note = ""
        if conc_diverge:
            finding_note = f" The paper's reported finding did not reproduce ({_concordance_desc(conc_diverge[0])})."
        elif conc_partial:
            # A metric diverged, but a finding concordance was strong-but-partial: surface it so the
            # human sees the divergence and the partial reproduction side by side.
            finding_note = f" A reported finding partially reproduced ({_concordance_desc(conc_partial[0])})."
        if attribution["our_side"] == "cleared":
            classification = "not_validated"
            reasoning = (
                f"{n_div} finding/metric(s) diverge beyond tolerance and our side was cleared "
                "(confident pipeline equivalent, recognized reference build), so the run did not reproduce "
                "the paper's values in our hands." + finding_note
            )
        else:
            classification = "inconclusive"
            reasoning = (
                f"{n_div} finding/metric(s) diverge, but our side could not be cleared "
                f"({'; '.join(attribution['reasons'])}), so the divergence cannot be attributed to the paper."
                + finding_note
            )
        auto_finalize = False

    if veto_lifted:
        # The explanation IS the product for an assessment feature; the label summarises it. State the
        # divergence prominently rather than letting a clean-looking `validated` hide it, and hold the
        # study for a human, as every Level-3 validated already does.
        auto_finalize = False
        reasoning += " " + " ".join(
            divergence_attribution[c["mapped_key"]]["explanation"] for c in explained_divergences
        )
        reasoning += (
            f" {len(explained_divergences)} technical QC metric(s) diverge for a known reason and are "
            "reported rather than counted against the paper; confirm before finalizing."
        )

    if coverage["advisory"]:
        reasoning += (
            f" ({coverage['advisory']} peak-count claim(s) were surfaced as advisory evidence and not "
            "scored: a condition/consensus-qualified peak count is not directly comparable to the "
            "per-sample computed count.)"
        )

    return {
        "comparisons": comparisons,
        "attribution": attribution,
        # Per-metric: which divergences a named tool-pair difference accounts for, and the sentence
        # that says so. Rendered next to the verdict; a divergence absent from here is unexplained.
        "divergence_attribution": divergence_attribution,
        "coverage": coverage,
        "classification": classification,
        "auto_finalize": auto_finalize,
        "reasoning": reasoning,
    }
