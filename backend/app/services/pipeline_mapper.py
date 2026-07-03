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

_RNASEQ = ("nf-core/rnaseq", "3.14.0")
_SCRNASEQ = ("nf-core/scrnaseq", "2.7.1")


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
    elif any(m in a for m in _BULK_RNA_MARKERS):
        key, ver = _RNASEQ
    else:
        return PipelineMapping(
            pipeline_key=None,
            pipeline_version=None,
            mapping_confidence="none",
            mapping_notes=f"No supported nf-core equivalent identified for assay '{assay or 'unknown'}'.",
            blockers=[f"no nf-core equivalent for method: {assay or 'unknown'}"],
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
