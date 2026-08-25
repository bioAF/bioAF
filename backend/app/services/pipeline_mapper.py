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


@dataclass(frozen=True)
class AssayRoute:
    """One hand-verified assay -> nf-core pipeline mapping.

    A route is declared only after someone has read the pipeline's own module sources and confirmed
    what it emits (``local/lit_validation_refine/plan_1.md``). Whether the route can also reach a
    finding-tier verdict is a separate declaration, in ``validation_level3_service._WIRING``; a
    route with no wiring entry still maps, launches and produces QC evidence, and the C1 gate says
    plainly that no finding set can be reproduced.
    """

    pipeline_key: str
    pipeline_version: str
    # Lowercased substrings looked for in the paper's assay string. Keep them specific: a marker
    # broad enough to appear in a neighbouring subfield's prose mis-routes that subfield's papers.
    markers: tuple[str, ...]


# ORDER IS LAW. Routes are tried top to bottom and the first marker hit wins, so a narrow assay
# that borrows a broader one's words must be declared above it: "single-cell RNA-seq" contains the
# bulk RNA marker "rna-seq", and ATAC papers name MACS2 just as ChIP papers do.
# `test_pipeline_mapper.test_every_route_maps_every_marker_it_claims` is what holds this order.
_ROUTES: tuple[AssayRoute, ...] = (
    # Single-cell first: "single-cell RNA-seq" also matches the bulk markers.
    AssayRoute(
        pipeline_key="nf-core/scrnaseq",
        pipeline_version="2.7.1",
        markers=("single-cell", "single cell", "scrna", "sc-rna", "snrna", "10x", "chromium", "cell ranger"),
    ),
    # ATAC before ChIP: an ATAC paper won't say rna-seq, but both call peaks with MACS2, and
    # "chromatin" appears in both subfields' prose.
    AssayRoute(
        pipeline_key="nf-core/atacseq",
        pipeline_version="2.1.2",
        markers=(
            "atac-seq",
            "atac seq",
            "atacseq",
            "transposase-accessible",
            "transposase accessible",
            "assay for transposase",
            "chromatin accessibility",
        ),
    ),
    # Kept specific on purpose: a vague "bespoke ChIP variant" must stay unmappable
    # (not_reproducible), so bare "chip" is NOT a marker. macs2 is excluded because ATAC-seq also
    # uses it (it would mis-route ATAC papers to chipseq).
    AssayRoute(
        pipeline_key="nf-core/chipseq",
        pipeline_version="2.1.0",
        markers=(
            "chip-seq",
            "chip seq",
            "chipseq",
            "chip-sequencing",
            "chromatin immunoprecipitation",
            "histone mark",
            "histone modification",
            "h3k",
        ),
    ),
    AssayRoute(
        pipeline_key="nf-core/rnaseq",
        pipeline_version="3.14.0",
        markers=("rna-seq", "rnaseq", "bulk rna", "transcriptom", "mrna-seq"),
    ),
)


@dataclass
class PipelineMapping:
    pipeline_key: str | None
    pipeline_version: str | None
    mapping_confidence: str  # "exact" | "partial" | "none"
    mapping_notes: str
    blockers: list[str] = field(default_factory=list)


def _mentions_nf_core(tools: list[str]) -> bool:
    return any("nf-core" in (t or "").lower() or "nfcore" in (t or "").lower() for t in tools)


def _match_route(assay: str) -> AssayRoute | None:
    """The first declared route whose marker appears in ``assay``, or None."""
    for route in _ROUTES:
        if any(marker in assay for marker in route.markers):
            return route
    return None


def map_method(
    assay: str | None, tools: list[str] | None = None, reference_build: str | None = None
) -> PipelineMapping:
    """Map ``assay`` (with optional tool hints) to a supported nf-core pipeline."""
    a = (assay or "").lower()
    tools = tools or []
    tool_str = ", ".join(t for t in tools if t) or "unspecified"

    route = _match_route(a)
    if route is None:
        if not a.strip():
            # No assay identifiable at all: the methods are too thin to select a pipeline. The
            # classifier reads this as `missing_methods`, distinct from `not_reproducible` (a known
            # but unsupported method). The marker blocker is the stable signal the early-exit keys
            # off.
            return PipelineMapping(
                pipeline_key=None,
                pipeline_version=None,
                mapping_confidence="none",
                mapping_notes=(
                    "No assay could be identified from the paper; the methods are too thin to select a pipeline."
                ),
                blockers=["insufficient method detail to identify an assay"],
            )
        return PipelineMapping(
            pipeline_key=None,
            pipeline_version=None,
            mapping_confidence="none",
            mapping_notes=f"No supported nf-core equivalent identified for assay '{assay}'.",
            blockers=[f"no nf-core equivalent for method: {assay}"],
        )

    key, ver = route.pipeline_key, route.pipeline_version
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
