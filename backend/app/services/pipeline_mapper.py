"""B3: map an extracted method/assay to an installable nf-core pipeline (lit_validation).

Pure, auditable mapping from a paper's assay to a supported nf-core pipeline + version, with a
confidence and rationale. This is where the "equivalent nf-core pipeline" assumption is enforced:
real papers rarely map 1:1 (custom references, legacy toolchains, unsupported chemistries), so a
match defaults to ``partial`` unless the paper already used the nf-core pipeline. No equivalent ->
``none`` plus a blocker, which the classifier reads as ``not_reproducible``.

Shared with ai_pipeline_run (recommend-pipeline); keep it side-effect free.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache


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
    # DIAGNOSTIC markers: lowercased, each anchored at its START (see `_marker_pattern`), and each
    # one enough to identify the assay on its own. Keep them specific: a marker broad enough to
    # appear in a neighbouring subfield's prose mis-routes that subfield's papers.
    markers: tuple[str, ...]
    # CONTEXTUAL markers: true of this assay and equally true of its neighbours. `rna-seq` is as
    # true of gene fusion, alternative splicing and dual RNA-seq as it is of bulk RNA-seq, so a
    # paper carrying nothing else has named a FAMILY, not a pipeline.
    #
    # This is a demotion, never a deletion. A contextual match is the family's answer of last
    # resort -- read `rna-seq` as "nf-core/rnaseq unless something else earns it" -- and it is what
    # a paper that says only "RNA sequencing" should still get, because such a paper genuinely
    # carries no evidence separating rnaseq from rnasplice. Rarity cannot rank what was never
    # mentioned. What the demotion buys is that genuinely diagnostic evidence (a rare registry
    # topic, a tool the paper named) is now ALLOWED TO COMPETE, where before the marker returned
    # first and the registry fallback never ran at all.
    contextual_markers: tuple[str, ...] = ()


# ORDER IS LAW. Routes are tried top to bottom and the first marker hit wins, so a narrow assay
# that borrows a broader one's words must be declared above it: "single-cell RNA-seq" contains the
# bulk RNA marker "rna-seq", and ATAC papers name MACS2 just as ChIP papers do.
# `test_pipeline_mapper.test_every_route_maps_every_marker_it_claims` is what holds this order.
_ROUTES: tuple[AssayRoute, ...] = (
    # Single-cell first: "single-cell RNA-seq" also matches the bulk markers.
    AssayRoute(
        pipeline_key="nf-core/scrnaseq",
        pipeline_version="2.7.1",
        markers=("single-cell", "single cell", "scrna", "sc-rna", "snrna", "chromium", "cell ranger"),
        # 10x sells the chemistry for spatial (Visium) and for single-cell ATAC as well as for
        # scRNA-seq, so "10x" names the vendor rather than the assay. It captured every spatial
        # transcriptomics paper for scrnaseq.
        contextual_markers=("10x",),
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
            # scATAC-seq and snATAC-seq carried `atac-seq` mid-word and matched on containment
            # alone. Anchoring the marker drops them unless the spellings are declared, and a paper
            # writes them exactly this way. Both still lose to the single-cell route above when the
            # paper spells "single-cell" out, which is what a multiome paper does and is unchanged.
            "scatac",
            "snatac",
            "transposase-accessible",
            "transposase accessible",
            "assay for transposase",
            "chromatin accessibility",
        ),
    ),
    # CUT&RUN and CUT&Tag before ChIP-seq, and this ordering is load-bearing rather than tidy:
    # these assays target the same histone marks, so a realistic CUT&RUN assay string ("CUT&RUN for
    # H3K27me3") also carries the chipseq marker "h3k". Declared below chipseq, every one of these
    # papers would silently run the wrong pipeline.
    #
    # Route A (Level-2 only) and deliberately so. Verified from the source, not the docs:
    # cutandrun 3.2.2 has no featureCounts, no DESeq2 and no differential module anywhere in
    # conf/modules.config; its consensus output is BED-shaped (`.consensus.peaks`,
    # `.consensus.peak_counts`, lines 747-768) and CONSENSUS_PEAK_COUNTS is a PEAK_QC step, not a
    # peaks x samples matrix. So there is no Level-3 route and `_WIRING` has no entry, which the
    # C1 gate states rather than offering a control that leads nowhere. A CUT&RUN paper's headline
    # claim IS a peak count, and peak_count is the one finding-tier scalar, so a Level-2 verdict
    # here can still reach `validated`.
    AssayRoute(
        pipeline_key="nf-core/cutandrun",
        pipeline_version="3.2.2",
        markers=(
            "cut&run",
            "cut & run",
            "cut-and-run",
            "cut and run",
            "cutandrun",
            "cut&tag",
            "cut & tag",
            "cut-and-tag",
            "cut and tag",
            "cutandtag",
            "cuttag",
            "cut run",
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
        ),
        # A histone mark is the TARGET, and ChIP-seq, CUT&RUN, CUT&Tag and ChIP-exo all read it.
        # Naming one says which protein was profiled, not which protocol did the profiling.
        contextual_markers=("h3k",),
    ),
    # Amplicon microbiome work. Markers name the amplicon, never the field: "microbiome" and
    # "metagenomics" belong equally to nf-core/mag (shotgun), which has a different output, and
    # bare "its" is an English word. A vaguer microbiome string is left to the registry fallback,
    # which is where an ambiguous match belongs.
    #
    # Route B, verified from the source: DADA2_MERGE publishes into `${outdir}/dada2`
    # (conf/modules.config @ 2.18.0, lines 265-271) and emits `path("ASV_table.tsv")`
    # (modules/local/dada2_merge.nf). The script transposes the DADA2 sequence table, names each row
    # by an md5 of its sequence, reorders to [ASV_ID, samples..., sequence], drops `sequence`, and
    # writes tab-separated with `row.names = FALSE`. So the shape is an `ASV_ID` column followed by
    # per-sample integer counts, which de_bulk_deseq2 consumes unchanged.
    AssayRoute(
        pipeline_key="nf-core/ampliseq",
        pipeline_version="2.18.0",
        markers=(
            "16s",
            "18s rrna",
            "its amplicon",
            "its1",
            "its2",
            "metabarcoding",
        ),
        # An amplicon library is defined by its primers, not its subject. SARS-CoV-2 ARTIC tiling is
        # amplicon sequencing and belongs to nf-core/viralrecon; a CRISPR edit site is amplicon
        # sequencing and belongs to nf-core/crisprseq. 16S is merely the commonest case.
        contextual_markers=("amplicon sequencing", "amplicon-sequencing"),
    ),
    # Small RNA before bulk: "small RNA-seq" contains "rna-seq". Markers name the molecule
    # (mirna/microrna) or qualify the RNA (small/smrna), never bare "rna", which would swallow the
    # whole transcriptomics literature.
    #
    # Route B (a full finding-tier verdict) because nf-core/smrnaseq already emits the matrix the
    # bulk DESeq2 notebook consumes: DATATABLE_MERGE publishes `mirna.tsv` into
    # `mirna_quant/mirtop/` (conf/modules.config @ 2.4.1, lines 485-491), written by
    # bin/collapse_mirtop.r as `mirna = counts[, lapply(.SD, sum), by = miRNA]` with
    # `sep = "\t", row.names = FALSE`, so the shape is a `miRNA` column followed by per-sample
    # integer counts. Nothing in the DE notebook is RNA-specific.
    AssayRoute(
        pipeline_key="nf-core/smrnaseq",
        pipeline_version="2.4.1",
        markers=(
            "small rna",
            "small-rna",
            "smrna",
            # "srna" catches the abbreviations papers actually use in their own methods -- sRNAseq,
            # sRNA-seq, tsRNA -- every one of which contains "rnaseq" or "rna-seq" and would
            # otherwise fall through to bulk. A real paper (Roy et al. 2023, GSE221185) calls its
            # assay sRNAseq throughout, and without this it would have launched a bulk
            # quantification against 20 bp reads and answered confidently.
            "srna",
            # tRNA-derived small RNA, which carried `srna` mid-word until the marker was anchored.
            # A real assay a paper names in its own methods, so it is declared rather than caught by
            # accident.
            "tsrna",
            "sncrna",
            "small non-coding",
            "mirna",
            "microrna",
            "mir-seq",
            "pirna",
        ),
    ),
    AssayRoute(
        pipeline_key="nf-core/rnaseq",
        pipeline_version="3.14.0",
        # `lncrna` names the molecule, the way smrnaseq's markers do. Long non-coding RNA-seq IS
        # bulk RNA-seq and nf-core/rnaseq is the right pipeline for it; it reached that answer only
        # because "lncRNA-seq" contains "rna-seq", and anchoring the marker would have refused it.
        markers=("bulk rna", "mrna-seq", "lncrna"),
        # The four measured mis-routers. Gene fusion, alternative splicing, dual host-pathogen and
        # metatranscriptomics papers all say "RNA-seq" and all mean a different pipeline, and every
        # one of those pipelines has no declared route, so no reordering of this table can help.
        contextual_markers=("rna-seq", "rnaseq", "transcriptom"),
    ),
)


# ---- what the DEPOSITED DATA says it is, as opposed to what the paper's prose says ----
#
# A paper is prose, and prose is compound. "RRBS and RNA-seq" names two assays; `_match_route`
# returns the first marker hit in declaration order, and `rna-seq` is a declared marker, so any
# modern paper that also ran RNA-seq routed to rnaseq no matter which dataset was scoped. Two real
# papers (GSE213770 and GSE228658, both Bisulfite-Seq) were planned against atacseq and rnaseq on
# exactly that mechanism and declined.
#
# The accession a study was scoped to is not prose. ENA records a controlled `library_strategy` per
# run, chosen by the depositor from a fixed vocabulary, and that is a statement about the data
# itself rather than a reading of a methods section. Where the two disagree, the data wins.
#
# Two separate declarations per strategy, because they answer two different questions:
#
#   `pipeline_key`  which pipeline to ROUTE to. None for a strategy too broad to decide on: ENA
#                   files bulk, single-cell, total and ribo-depleted RNA under one `RNA-Seq` value,
#                   so routing on it would send every scRNA-seq study to bulk rnaseq. Worse than
#                   the prose it would be overriding.
#   `compatible`    every pipeline that legitimately consumes this strategy, which is what says a
#                   prose route may STAND. ENA has no CUT&RUN value, so CUT&RUN and CUT&Tag runs are
#                   deposited as `ChIP-Seq`; without this, routing on the strategy would silently
#                   undo the deliberate cutandrun-above-chipseq ordering above.
#
# Only strategies someone has reasoned about are declared. An undeclared strategy has NO OPINION
# (the prose route stands untouched), which is what keeps this from becoming a second, worse mapper.


@dataclass(frozen=True)
class LibraryStrategyRoute:
    """One INSDC ``library_strategy`` value, and what it says about which pipeline may run."""

    strategy: str
    pipeline_key: str | None
    compatible: tuple[str, ...]


def _strategy_key(strategy: str | None) -> str:
    """Fold an INSDC strategy to a comparable token: ``Bisulfite-Seq`` and ``bisulfite seq`` agree."""
    return re.sub(r"[^a-z0-9]+", "", (strategy or "").lower())


_RNA_PIPELINES: tuple[str, ...] = (
    "nf-core/rnaseq",
    "nf-core/scrnaseq",
    "nf-core/smrnaseq",
    "nf-core/rnasplice",
    "nf-core/rnafusion",
    "nf-core/circrna",
    "nf-core/dualrnaseq",
    "nf-core/isoseq",
    "nf-core/differentialabundance",
    "nf-core/spatialvi",
)

_LIBRARY_STRATEGY_ROUTES: tuple[LibraryStrategyRoute, ...] = (
    LibraryStrategyRoute("ATAC-seq", "nf-core/atacseq", ("nf-core/atacseq",)),
    LibraryStrategyRoute("DNase-Hypersensitivity", "nf-core/atacseq", ("nf-core/atacseq",)),
    LibraryStrategyRoute("Bisulfite-Seq", "nf-core/methylseq", ("nf-core/methylseq",)),
    # ENA has no CUT&RUN or CUT&Tag value; those runs are deposited as ChIP-Seq.
    LibraryStrategyRoute("ChIP-Seq", "nf-core/chipseq", ("nf-core/chipseq", "nf-core/cutandrun")),
    LibraryStrategyRoute("Hi-C", "nf-core/hic", ("nf-core/hic",)),
    LibraryStrategyRoute("ChIA-PET", "nf-core/hic", ("nf-core/hic",)),
    LibraryStrategyRoute("miRNA-Seq", "nf-core/smrnaseq", ("nf-core/smrnaseq",)),
    # An amplicon library is defined by its primers, not its subject: 16S microbiome, a CRISPR edit
    # site and a targeted panel are all AMPLICON. ampliseq is the default because a 16S study is the
    # common case, and a paper whose prose already reached crisprseq or ampliseq keeps it.
    LibraryStrategyRoute("AMPLICON", "nf-core/ampliseq", ("nf-core/ampliseq", "nf-core/crisprseq")),
    # Declared but deliberately unrouted: too broad to decide on, still useful to the guard.
    LibraryStrategyRoute("RNA-Seq", None, _RNA_PIPELINES),
    LibraryStrategyRoute("ssRNA-seq", None, _RNA_PIPELINES),
    LibraryStrategyRoute("ncRNA-Seq", None, _RNA_PIPELINES),
)


def library_strategy_routes() -> dict[str, LibraryStrategyRoute]:
    """Every declared strategy, keyed by its folded token."""
    return {_strategy_key(r.strategy): r for r in _LIBRARY_STRATEGY_ROUTES}


def route_for_library_strategy(strategy: str | None) -> LibraryStrategyRoute | None:
    """What a deposited ``library_strategy`` says about pipeline choice, or None for no opinion."""
    return library_strategy_routes().get(_strategy_key(strategy))


# The stable signal a caller keys off to recognize this blocker among a plan's others, mirroring how
# `_early_exit_classification` keys off "insufficient method detail". It is deliberately a phrase
# from the sentence rather than a code, so the blocker stays one readable sentence for a scientist.
LIBRARY_STRATEGY_CONFLICT_MARKER = "does not consume"


def library_strategy_conflict(pipeline_key: str | None, strategy: str | None) -> str | None:
    """Why ``pipeline_key`` must not be run against data deposited as ``strategy``, or None.

    A guard, not a router. ``resolve_pipeline_for_assay`` already prefers the deposit where it can,
    but it can only offer a pipeline this instance is able to run; where it cannot, the prose route
    stands and the plan still names a pipeline that would read the wrong data and answer
    confidently. Study 14 was planned as nf-core/atacseq over Bisulfite-Seq at
    ``mapping_confidence: exact`` and nothing objected.

    Silence is not evidence: a strategy nobody has reasoned about, or a plan with no pipeline at
    all, yields None rather than a block.
    """
    if not pipeline_key:
        return None
    route = route_for_library_strategy(strategy)
    if route is None or pipeline_key in route.compatible:
        return None
    remedy = f" {route.pipeline_key} is the pipeline for {route.strategy} data." if route.pipeline_key else ""
    return (
        f"{pipeline_key} {LIBRARY_STRATEGY_CONFLICT_MARKER} {route.strategy} data, and the accession "
        f"this study was scoped to is deposited as {route.strategy}. Running it would spend the "
        f"compute and answer confidently about the wrong thing.{remedy}"
    )


def is_library_strategy_conflict(blocker: str | None) -> bool:
    """Whether a recorded plan blocker is the deposit-contradicts-pipeline refusal above."""
    return LIBRARY_STRATEGY_CONFLICT_MARKER in (blocker or "")


def deposit_conflict(blockers: list | None, library_strategy: str | None) -> dict | None:
    """The plan's fatal blocker and the pipeline that would resolve it, or None.

    A plan carries blockers of two kinds and they used to be indistinguishable. Most are advisory:
    the paper named two reference builds, the sample ids are not listed. Exactly one refuses the
    approval outright. Rendered as one bullet among the others, the scientist learned which was
    which by clicking Approve and reading a 400, with no control anywhere that resolved it.

    ``suggested_pipeline_key`` is None when the deposited strategy is declared but deliberately
    unrouted (RNA-Seq is too broad to name one pipeline for), which is honest rather than empty: the
    deposit can refuse a plan without being able to propose the replacement, and the gate then has
    only the override to offer.
    """
    message = next((b for b in (blockers or []) if is_library_strategy_conflict(b)), None)
    if message is None:
        return None
    route = route_for_library_strategy(library_strategy)
    return {
        "message": message,
        "suggested_pipeline_key": route.pipeline_key if route else None,
        "library_strategy": (library_strategy or "").strip() or None,
    }


@dataclass
class PipelineMapping:
    pipeline_key: str | None
    pipeline_version: str | None
    mapping_confidence: str  # "exact" | "partial" | "none"
    mapping_notes: str
    blockers: list[str] = field(default_factory=list)


def _mentions_nf_core(tools: list[str]) -> bool:
    return any("nf-core" in (t or "").lower() or "nfcore" in (t or "").lower() for t in tools)


def declared_route_version(pipeline_key: str | None) -> str | None:
    """The version a hand-verified route pins for ``pipeline_key``, or None when none declares it.

    A pipeline can be selected by something other than its markers (the deposit's own library
    strategy, say). When it is one this table already covers, the plan must still record the PINNED
    version: it is what the catalog installs, what Level-3 wiring was verified against, and what a
    rerun reproduces.
    """
    for route in _ROUTES:
        if route.pipeline_key == pipeline_key:
            return route.pipeline_version
    return None


# A marker must BEGIN a word, and may end in the middle of one.
#
# Matching was plain substring containment, and `transcriptom` therefore matched inside
# `metatranscriptomics`, capturing every metatranscriptomics paper for nf-core/rnaseq. A declared
# route short-circuits everything (`_match_route` returns the first hit and the registry fallback
# never runs), so that did not out-score nf-core/metatdenovo, it stopped metatdenovo from ever being
# considered.
#
# Anchoring BOTH ends is the obvious fix and was measured to be the wrong one. Several markers are
# deliberate stems: `transcriptom` is one marker covering transcriptome, transcriptomic and
# transcriptomics, and requiring a word boundary after it refuses all three. So the anchor is a
# prefix check only: a non-word character (or the start of the string) has to come before the
# marker, and the word may run on after it.
#
# The check is on the character BEFORE the marker, never inside it, so a marker's own punctuation is
# untouched: `cut&run`, `atac-seq` and `16s` all match as written.


@lru_cache(maxsize=512)
def _marker_pattern(marker: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z0-9])" + re.escape(marker))


def marker_matches(marker: str, assay: str) -> bool:
    """Whether ``marker`` starts a word in ``assay``."""
    return _marker_pattern(marker).search(assay) is not None


def match_route(assay: str) -> tuple[AssayRoute, bool] | None:
    """The declared route ``assay`` names, and whether it named it DIAGNOSTICALLY.

    The flag is what a caller keys off to decide whether the route may short-circuit. A diagnostic
    match is an answer. A contextual match is a FLOOR: the family's answer of last resort, which
    stands unless something genuinely diagnostic displaces it.

    Diagnostic markers are swept across every route BEFORE any contextual marker is considered, so
    a paper that identifies its assay outright is never answered by a word that merely describes its
    family. Within each tier, declaration order is still law.
    """
    for route in _ROUTES:
        if any(marker_matches(marker, assay) for marker in route.markers):
            return route, True
    for route in _ROUTES:
        if any(marker_matches(marker, assay) for marker in route.contextual_markers):
            return route, False
    return None


def _match_route(assay: str) -> AssayRoute | None:
    """The route ``assay`` names on any evidence, diagnostic or contextual, or None."""
    match = match_route(assay)
    return match[0] if match else None


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
