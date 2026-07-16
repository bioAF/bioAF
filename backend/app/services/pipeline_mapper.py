"""B3: map an extracted method/assay to an installable nf-core pipeline (lit_validation).

Pure, auditable mapping from a paper's assay to a supported nf-core pipeline + version, with a
confidence and rationale. This is where the "equivalent nf-core pipeline" assumption is enforced:
real papers rarely map 1:1 (custom references, legacy toolchains, unsupported chemistries), so a
match defaults to ``partial`` unless the paper already used the nf-core pipeline. No equivalent ->
``none`` plus a blocker, which the classifier reads as ``not_reproducible``.

Shared with ai_pipeline_run (recommend-pipeline); keep it side-effect free.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Canonical supported assays -> (pipeline_key, default version). Versions match the built-in catalog
# (pipeline_catalog_service.BUILTIN_PIPELINES); D2/D3 reconcile against the org's live catalog.
_SCRNA_MARKERS = ("single-cell", "single cell", "scrna", "sc-rna", "snrna", "10x", "chromium", "cell ranger")
_BULK_RNA_MARKERS = ("rna-seq", "rnaseq", "bulk rna", "transcriptom", "mrna-seq")
# Kept specific on purpose: a vague "bespoke ChIP variant" must stay unmappable (not_reproducible),
# so bare "chip" is NOT a marker. macs2 is excluded because ATAC-seq also uses it (would mis-route
# ATAC papers to chipseq until atacseq has its own extractor).
_CHIP_MARKERS = (
    "chip-seq", "chip seq", "chipseq", "chip-sequencing",
    "chromatin immunoprecipitation", "histone mark", "histone modification", "h3k",
)
# ATAC-seq markers. Kept specific ("atac"/"transposase-accessible"/"assay for transposase") so an
# unrelated assay does not mis-route here. Checked before ChIP/RNA (an ATAC paper won't say rna-seq).
_ATAC_MARKERS = (
    "atac-seq", "atac seq", "atacseq", "transposase-accessible", "transposase accessible",
    "assay for transposase", "chromatin accessibility",
)

_RNASEQ = ("nf-core/rnaseq", "3.14.0")
_SCRNASEQ = ("nf-core/scrnaseq", "2.7.1")
_CHIPSEQ = ("nf-core/chipseq", "2.1.0")
_ATACSEQ = ("nf-core/atacseq", "2.1.2")


@dataclass
class PipelineMapping:
    pipeline_key: str | None
    pipeline_version: str | None
    mapping_confidence: str  # "exact" | "partial" | "none"
    mapping_notes: str
    blockers: list[str] = field(default_factory=list)


def _mentions_nf_core(tools: list[str]) -> bool:
    return any("nf-core" in (t or "").lower() or "nfcore" in (t or "").lower() for t in tools)


def map_method(assay: str | None, tools: list[str] | None = None, reference_build: str | None = None) -> PipelineMapping:
    """Map ``assay`` (with optional tool hints) to a supported nf-core pipeline."""
    a = (assay or "").lower()
    tools = tools or []
    tool_str = ", ".join(t for t in tools if t) or "unspecified"

    # Single-cell markers take precedence: "single-cell RNA-seq" also matches the bulk markers.
    if any(m in a for m in _SCRNA_MARKERS):
        key, ver = _SCRNASEQ
    elif any(m in a for m in _ATAC_MARKERS):
        key, ver = _ATACSEQ
    elif any(m in a for m in _CHIP_MARKERS):
        key, ver = _CHIPSEQ
    elif any(m in a for m in _BULK_RNA_MARKERS):
        key, ver = _RNASEQ
    elif not a.strip():
        # No assay identifiable at all: the methods are too thin to select a pipeline. The classifier
        # reads this as `missing_methods`, distinct from `not_reproducible` (a known but unsupported
        # method). The marker blocker is the stable signal the early-exit keys off.
        return PipelineMapping(
            pipeline_key=None,
            pipeline_version=None,
            mapping_confidence="none",
            mapping_notes="No assay could be identified from the paper; the methods are too thin to select a pipeline.",
            blockers=["insufficient method detail to identify an assay"],
        )
    else:
        return PipelineMapping(
            pipeline_key=None,
            pipeline_version=None,
            mapping_confidence="none",
            mapping_notes=f"No supported nf-core equivalent identified for assay '{assay}'.",
            blockers=[f"no nf-core equivalent for method: {assay}"],
        )

    if _mentions_nf_core(tools):
        confidence = "exact"
        notes = f"Paper's method already uses {key}; mapped directly (tools: {tool_str})."
    else:
        confidence = "partial"
        notes = (
            f"Mapped assay '{assay}' (tools: {tool_str}) to {key} {ver}. The original toolchain "
            f"differs from nf-core, so exact metric reproduction is not guaranteed."
        )
    return PipelineMapping(pipeline_key=key, pipeline_version=ver, mapping_confidence=confidence, mapping_notes=notes)
