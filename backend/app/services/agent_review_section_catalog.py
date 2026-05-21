"""Section catalog for the Agent Review prompt builder.

Each top-level section has an id, label, optional experiment-only flag, and a
list of sub-items. Each sub-item has an id, label, default_on flag, and a
prompt fragment that gets concatenated into the assembled prompt when the
sub-item is selected.

The catalog is shipped with the release; sub-item ids are stable strings so
historical reviews remain auditable across upgrades.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SubItem:
    id: str
    label: str
    default_on: bool
    prompt_fragment: str


@dataclass(frozen=True)
class Section:
    id: str
    label: str
    experiment_only: bool
    sub_items: list[SubItem]


SECTIONS: list[Section] = [
    Section(
        id="qc",
        label="Quality control and technical assessment",
        experiment_only=False,
        sub_items=[
            SubItem(
                id="qc.metric_review",
                label="QC metric review",
                default_on=True,
                prompt_fragment=(
                    "Flag samples outside expected ranges on QC metrics (read depth, alignment rate, "
                    "duplication rate, GC content, adapter contamination, Q30 scores, insert size "
                    "distributions). Compare against user thresholds where present, or typical "
                    "ranges for the assay where they are not."
                ),
            ),
            SubItem(
                id="qc.outlier_detection",
                label="Outlier detection",
                default_on=True,
                prompt_fragment=(
                    "Identify samples that are statistical outliers across the cohort on key "
                    "technical metrics, even if they pass absolute thresholds."
                ),
            ),
            SubItem(
                id="qc.batch_effect_indicators",
                label="Batch effect indicators",
                default_on=True,
                prompt_fragment=(
                    "Surface systematic differences correlating with processing date, sequencer, "
                    "lane, flow cell, library prep batch, or operator."
                ),
            ),
            SubItem(
                id="qc.failed_samples_summary",
                label="Failed or borderline samples",
                default_on=True,
                prompt_fragment=("Summarize which samples failed which checks and at what severity, for quick triage."),
            ),
        ],
    ),
    Section(
        id="metadata",
        label="Sample metadata patterns",
        experiment_only=False,
        sub_items=[
            SubItem(
                id="metadata.outcome_correlations",
                label="Metadata-outcome correlations",
                default_on=True,
                prompt_fragment=(
                    "Note associations between sample attributes (tissue type, collection date, "
                    "storage conditions, RIN, input amount, age, sex, treatment group) and downstream "
                    "technical metrics."
                ),
            ),
            SubItem(
                id="metadata.missing_or_inconsistent",
                label="Missing or inconsistent metadata",
                default_on=True,
                prompt_fragment=(
                    "Flag incomplete metadata, inconsistent labeling, or values that look like data entry errors."
                ),
            ),
            SubItem(
                id="metadata.cohort_composition",
                label="Cohort composition",
                default_on=True,
                prompt_fragment=(
                    "Summarize the experimental design as inferred from metadata: group sizes, "
                    "balance across covariates, and potential confounders."
                ),
            ),
        ],
    ),
    Section(
        id="bio",
        label="Pipeline-specific biological signals",
        experiment_only=False,
        sub_items=[
            SubItem(
                id="bio.bulk_rnaseq",
                label="Bulk RNA-seq",
                default_on=True,
                prompt_fragment=(
                    "If this looks like a bulk RNA-seq run, comment on rRNA contamination, strand "
                    "specificity, gene body coverage uniformity, exonic vs intronic ratios, top "
                    "expressed genes, and library complexity."
                ),
            ),
            SubItem(
                id="bio.single_cell",
                label="Single-cell",
                default_on=True,
                prompt_fragment=(
                    "If this looks like a single-cell run, comment on cells per sample vs expected, "
                    "median genes/UMIs per cell, mitochondrial percentage distributions, doublet "
                    "rate estimates, ambient RNA indicators, clustering stability, and expected "
                    "cell type presence."
                ),
            ),
            SubItem(
                id="bio.variant_calling",
                label="Variant calling / WGS / WES",
                default_on=True,
                prompt_fragment=(
                    "If this is variant calling / WGS / WES, comment on Ti/Tv ratios, het/hom "
                    "ratios, novel vs known variant proportions, coverage uniformity, contamination "
                    "estimates, and sex-check concordance with metadata."
                ),
            ),
            SubItem(
                id="bio.atac_chip",
                label="ATAC-seq / ChIP-seq",
                default_on=True,
                prompt_fragment=(
                    "If this is ATAC-seq or ChIP-seq, comment on TSS enrichment, FRiP scores, peak "
                    "counts and reproducibility, fragment size distributions, and blacklist region "
                    "overlap."
                ),
            ),
            SubItem(
                id="bio.methylation",
                label="Methylation",
                default_on=True,
                prompt_fragment=(
                    "If this is methylation data, comment on bisulfite conversion rates, CpG "
                    "coverage distribution, and methylation level distributions vs expected."
                ),
            ),
            SubItem(
                id="bio.proteomics",
                label="Proteomics / mass spec",
                default_on=True,
                prompt_fragment=(
                    "If this is proteomics / mass spec, comment on ID rates, missed cleavages, "
                    "mass accuracy drift, and contaminant proteins."
                ),
            ),
        ],
    ),
    Section(
        id="xsample",
        label="Cross-sample / experiment-level trends",
        experiment_only=True,
        sub_items=[
            SubItem(
                id="xsample.drift_over_time",
                label="Drift over time",
                default_on=True,
                prompt_fragment=(
                    "Look for performance changes across processing date, useful for spotting "
                    "reagent lot or instrument issues."
                ),
            ),
            SubItem(
                id="xsample.group_comparisons",
                label="Group comparisons",
                default_on=True,
                prompt_fragment=(
                    "Note whether treatment and control groups differ on technical metrics in ways "
                    "that could confound biological interpretation."
                ),
            ),
            SubItem(
                id="xsample.replicate_concordance",
                label="Replicate concordance",
                default_on=True,
                prompt_fragment=("Comment on how well replicates agree, with flags for unexpectedly divergent pairs."),
            ),
            SubItem(
                id="xsample.design_coverage",
                label="Coverage of experimental design",
                default_on=True,
                prompt_fragment=("Note whether the data supports planned analyses given sample counts and quality."),
            ),
        ],
    ),
    Section(
        id="interp",
        label="Interpretation and Recommendations",
        experiment_only=False,
        sub_items=[
            SubItem(
                id="interp.concerns_recommendations",
                label="Concerns and recommendations",
                default_on=True,
                prompt_fragment=(
                    "Provide a prioritized list of issues with suggested actions: rerun, exclude, "
                    "investigate, or consult the wet-lab team."
                ),
            ),
            SubItem(
                id="interp.confidence",
                label="Confidence assessment",
                default_on=True,
                prompt_fragment=(
                    "State your confidence in this summary and call out explicitly where data was missing or ambiguous."
                ),
            ),
            SubItem(
                id="interp.sanity_checks",
                label="Sanity checks",
                default_on=True,
                prompt_fragment=(
                    "Run sanity checks: do top results align with biological expectations given "
                    "metadata (sex concordance, tissue-specific markers, positive control behavior)?"
                ),
            ),
        ],
    ),
    Section(
        id="literature",
        label="Associated literature",
        experiment_only=False,
        sub_items=[
            SubItem(
                id="literature.results_consistency",
                label="Results vs associated literature",
                default_on=True,
                prompt_fragment=(
                    "If an Associated Literature section is provided, check the run's results and QC "
                    "against that prior work. Explicitly flag any result that is unexpected or that "
                    "contradicts the associated literature, and note notable agreement. When you "
                    "reference a paper cite it by title or DOI; when full text with page markers "
                    '(shown as "[Page N]") is provided, cite the specific page (for example, "p. 4").'
                ),
            ),
        ],
    ),
]


def all_sub_items() -> dict[str, SubItem]:
    """{subitem_id -> SubItem} for fast lookup during prompt assembly."""
    return {si.id: si for sec in SECTIONS for si in sec.sub_items}


def section_for_sub_item(subitem_id: str) -> Section | None:
    for sec in SECTIONS:
        for si in sec.sub_items:
            if si.id == subitem_id:
                return sec
    return None


def default_sub_item_ids(*, experiment_scope: bool) -> list[str]:
    """Return the sub-item ids that should be checked by default for the given
    review scope. Sections marked experiment_only are excluded from Button A."""
    ids: list[str] = []
    for sec in SECTIONS:
        if sec.experiment_only and not experiment_scope:
            continue
        for si in sec.sub_items:
            if si.default_on:
                ids.append(si.id)
    return ids
