"""Adding an assay to lit_validation is a declared unit of work, not five edits nobody checks.

A new assay touches the mapper's markers, the Level-3 wiring, the QC template map and the
samplesheet generators. Nothing checked that those agree, so a half-wired assay could reach a user
as a silent degrade: the paper maps, the run launches, and the verdict is capped with no statement
of why. These tests are that check, and they run against the declaration table rather than against
a hand-maintained list, so a route added tomorrow is covered the moment it is declared.
"""

from app.services.nf_core_registry_service import QC_TEMPLATE_MAP
from app.services.pipeline_mapper import (
    _ROUTES,
    match_route,
    is_library_strategy_conflict,
    library_strategy_conflict,
    library_strategy_routes,
    map_method,
    route_for_library_strategy,
)
from app.services.qc.templates import TEMPLATES
from app.services.validation_level3_service import _WIRING

# ---- the declaration table is internally consistent ----


def test_every_route_maps_every_marker_it_claims():
    """Route order is law, and this is what proves the order is right.

    Routes are tried top to bottom and the first marker hit wins, so a narrow assay that borrows a
    broad one's words ("small RNA-seq" contains "rna-seq") must be declared above it. Declaring it
    below is silent: the paper maps, to the wrong pipeline.
    """
    for route in _ROUTES:
        for marker in route.markers:
            mapping = map_method(marker)
            assert mapping.pipeline_key == route.pipeline_key, (
                f"{route.pipeline_key} claims the marker {marker!r}, but a paper whose assay is "
                f"exactly that maps to {mapping.pipeline_key}. A route declared above it is "
                f"swallowing the marker; move it up."
            )


def test_no_two_routes_claim_the_same_marker():
    """The same word cannot mean two pipelines. Whichever is declared first would always win, and
    the loser's entry would read as supported while never firing."""
    claimed: dict[str, str] = {}
    for route in _ROUTES:
        for marker in route.markers:
            assert marker not in claimed, f"{marker!r} is claimed by both {claimed[marker]} and {route.pipeline_key}"
            claimed[marker] = route.pipeline_key


def test_every_route_names_an_nf_core_pipeline_at_a_pinned_version():
    """The version is what the catalog installs and what the plan records. An unpinned route would
    silently drift to whatever the registry calls latest on the day the study ran."""
    for route in _ROUTES:
        assert route.pipeline_key.startswith("nf-core/"), route.pipeline_key
        assert route.pipeline_key.count("/") == 1, route.pipeline_key
        assert route.pipeline_version, f"{route.pipeline_key} declares no version"
        assert route.pipeline_version[0].isdigit(), (
            f"{route.pipeline_key} version {route.pipeline_version!r} is not a release tag"
        )


# ---- the declaration table agrees with everything downstream of it ----


def test_every_level3_route_names_a_pipeline_the_mapper_can_reach():
    """A Level-3 wiring entry for a pipeline no paper can map to is dead code that reads as reach.

    This is the orphan check: `_WIRING` is what `supported_finding_kinds` answers from, so an entry
    whose pipeline the mapper never names promises a finding-tier verdict that can never be asked
    for.
    """
    routable = {route.pipeline_key for route in _ROUTES}
    for pipeline_key, kind in _WIRING:
        assert pipeline_key in routable, (
            f"_WIRING has a '{kind}' route for {pipeline_key}, but no declared assay maps there, "
            f"so no paper can ever use it"
        )


def test_every_tailored_qc_template_is_registered():
    """QC_TEMPLATE_MAP is applied at install time and never validated. A typo there resolves to a
    template that does not exist, and the failure surfaces as an empty dashboard after a real run."""
    for pipeline_name, template_name in QC_TEMPLATE_MAP.items():
        assert template_name in TEMPLATES, (
            f"{pipeline_name} is mapped to the QC template {template_name!r}, which is not registered"
        )


# ---- a marker is anchored at its START, so it cannot match inside a longer word (plan_5.1 step 1) ----
#
# Markers were matched with plain substring containment, so `transcriptom` also matched inside
# `metatranscriptomics`. A declared route short-circuits everything: `_match_route` returns the
# first marker hit and the registry fallback never runs at all, so this did not merely out-score
# nf-core/metatdenovo, it stopped metatdenovo from ever being considered.


def test_a_marker_does_not_match_inside_a_longer_word():
    """Metatranscriptomics is its own assay with its own pipeline, and `transcriptom` was eating it.

    Answering nothing here is the fix, not a loss: no declared route covers metatranscriptomics, so
    the resolver's registry fallback is what should decide, and it never got the chance.
    """
    assert map_method("metatranscriptome de novo assembly").pipeline_key is None
    assert map_method("metatranscriptomics of the gut microbiome").pipeline_key is None


def test_a_marker_is_still_a_stem_at_its_end():
    """The other half of the guard, and the reason a full word boundary is the wrong fix.

    Several markers are deliberate stems: `transcriptom` exists to catch transcriptome,
    transcriptomic and transcriptomics in one. Anchoring both ends was measured to refuse all three
    and take ordinary bulk RNA-seq with them.
    """
    for assay in (
        "transcriptomic profiling of liver tissue",
        "transcriptome analysis",
        "transcriptomics of whole blood",
    ):
        assert map_method(assay).pipeline_key == "nf-core/rnaseq", assay


def test_the_assays_that_carried_a_marker_mid_word_are_declared_markers_now():
    """Anchoring drops every assay name that carried a marker mid-word, and three real ones did.

    scATAC-seq and snATAC-seq matched `atac-seq` inside a prefix; tsRNA matched `srna` inside one.
    Each is a real assay a paper writes, so each is declared rather than left to substring luck.
    """
    assert map_method("scATAC-seq").pipeline_key == "nf-core/atacseq"
    assert map_method("snATAC-seq").pipeline_key == "nf-core/atacseq"
    assert map_method("scATACseq").pipeline_key == "nf-core/atacseq"
    assert map_method("tsRNA profiling").pipeline_key == "nf-core/smrnaseq"
    assert map_method("lncRNA-seq").pipeline_key == "nf-core/rnaseq"


def test_the_anchor_is_a_prefix_check_and_not_a_word_boundary():
    """A marker's own punctuation is part of it. `cut&run`, `atac-seq` and `16s` all contain a
    character that a word-boundary anchor would break in the middle."""
    assert map_method("CUT&RUN for H3K27me3").pipeline_key == "nf-core/cutandrun"
    assert map_method("ATAC-seq of sorted nuclei").pipeline_key == "nf-core/atacseq"
    assert map_method("16S rRNA amplicon sequencing").pipeline_key == "nf-core/ampliseq"
    assert map_method("ChIP-seq for H3K27ac").pipeline_key == "nf-core/chipseq"


# ---- a marker is diagnostic or contextual, and it has to say which (plan_5.1 step 2) ----
#
# Today's markers are two different kinds of claim wearing one name. A DIAGNOSTIC marker identifies
# the assay on its own: `bulk rna`, `scrna`, `16s`, `chip-seq`. A CONTEXTUAL marker is true of the
# assay and equally true of its neighbours: `rna-seq` is as true of fusion, splicing and dual
# RNA-seq as it is of bulk, and it is the reason declaration order had to be load-bearing.
#
# A contextual marker is demoted, never discarded. It is the answer of LAST RESORT for its family:
# read `rna-seq` as "nf-core/rnaseq unless something else earns it". Demoting it without a floor was
# measured against the live registry and is worse than doing nothing, because a paper that says only
# "RNA sequencing" genuinely carries no evidence separating rnaseq from rnasplice.


def test_a_diagnostic_marker_identifies_the_assay_on_its_own():
    for assay, pipeline_key in (
        ("bulk RNA-seq", "nf-core/rnaseq"),
        ("scRNA-seq", "nf-core/scrnaseq"),
        ("ChIP-seq", "nf-core/chipseq"),
        ("ATAC-seq", "nf-core/atacseq"),
        ("16S rRNA gene sequencing", "nf-core/ampliseq"),
        ("miRNA-seq", "nf-core/smrnaseq"),
    ):
        match = match_route(assay.lower())
        assert match is not None, assay
        route, diagnostic = match
        assert diagnostic, f"{assay} should be diagnostic evidence for {pipeline_key}"
        assert route.pipeline_key == pipeline_key, assay


def test_a_contextual_marker_is_evidence_of_a_family_and_says_so():
    """These are the four markers measured to be mis-routing: `rna-seq` and `transcriptom` took
    fusion, splicing, dual and metatranscriptomics papers; `10x` took spatial; `amplicon sequencing`
    took viral amplicon; `h3k` is true of every histone assay there is."""
    for assay in (
        "RNA-seq of primary hepatocytes",
        "transcriptome analysis",
        "10x Visium spatial transcriptomics",
        "amplicon sequencing of the V4 region",
        "H3K27me3 profiling",
    ):
        match = match_route(assay.lower())
        assert match is not None, assay
        assert not match[1], f"{assay} carries contextual evidence only"


def test_no_marker_is_declared_both_diagnostic_and_contextual():
    """The two tiers are a classification, not two lists that happen to overlap. A marker in both
    would be diagnostic in practice and read as demoted."""
    for route in _ROUTES:
        overlap = set(route.markers) & set(route.contextual_markers)
        assert not overlap, f"{route.pipeline_key} declares {sorted(overlap)} as both kinds"


def test_every_route_maps_every_contextual_marker_it_claims():
    """The floor has to hold. A contextual marker that no longer reaches its own route is not a
    demotion, it is a deletion, and the paper it used to answer becomes a refusal."""
    for route in _ROUTES:
        for marker in route.contextual_markers:
            match = match_route(marker)
            assert match is not None, f"{route.pipeline_key} claims {marker!r}, which now matches nothing"
            assert match[0].pipeline_key == route.pipeline_key, (
                f"{route.pipeline_key} claims the contextual marker {marker!r}, which reaches "
                f"{match[0].pipeline_key} instead"
            )


def test_no_two_routes_claim_the_same_marker_of_either_kind():
    """The same word cannot mean two pipelines, whichever tier it is declared in."""
    claimed: dict[str, str] = {}
    for route in _ROUTES:
        for marker in (*route.markers, *route.contextual_markers):
            assert marker not in claimed, f"{marker!r} is claimed by both {claimed[marker]} and {route.pipeline_key}"
            claimed[marker] = route.pipeline_key


def test_the_floor_still_answers_every_paper_it_answered_before():
    """Step 2 declares the tiers and changes no answer. Demoting `rna-seq` and `transcriptom`
    without a scorer that can replace them turns ordinary bulk RNA-seq papers into refusals."""
    for assay in (
        "RNA-seq",
        "RNA-seq of primary hepatocytes",
        "transcriptomic profiling of liver tissue",
        "transcriptome analysis",
        "RNA-seq for gene fusion detection",
    ):
        assert map_method(assay).pipeline_key == "nf-core/rnaseq", assay
    assert map_method("10x Visium spatial transcriptomics").pipeline_key == "nf-core/scrnaseq"
    assert map_method("viral amplicon sequencing (ARTIC protocol)").pipeline_key == "nf-core/ampliseq"


# ---- the four proven assays are unchanged ----


def test_the_proven_assays_map_exactly_as_before():
    """Regression floor for every breadth change: the assays that already reach a verdict must keep
    mapping to the same pipeline at the same version."""
    assert map_method("bulk RNA-seq").pipeline_key == "nf-core/rnaseq"
    assert map_method("scRNA-seq").pipeline_key == "nf-core/scrnaseq"
    assert map_method("ChIP-seq").pipeline_key == "nf-core/chipseq"
    assert map_method("ATAC-seq").pipeline_key == "nf-core/atacseq"
    assert map_method("single-cell RNA-seq").pipeline_key == "nf-core/scrnaseq"
    versions = {route.pipeline_key: route.pipeline_version for route in _ROUTES}
    assert versions["nf-core/rnaseq"] == "3.14.0"
    assert versions["nf-core/scrnaseq"] == "2.7.1"
    assert versions["nf-core/chipseq"] == "2.1.0"
    assert versions["nf-core/atacseq"] == "2.1.2"


# ---- small RNA (nf-core/smrnaseq) ----


def test_a_small_rna_paper_maps_to_smrnaseq():
    """Small-RNA regulation is ordinary lab work, and until now every one of these papers ended at
    not_reproducible before any compute."""
    for assay in (
        "small RNA-seq",
        "smRNA-seq",
        "microRNA sequencing",
        "miRNA-seq",
        "small non-coding RNA profiling",
        "miR-seq of plasma exosomes",
    ):
        assert map_method(assay).pipeline_key == "nf-core/smrnaseq", assay


def test_the_small_rna_markers_do_not_swallow_bulk_rnaseq():
    """ "small RNA-seq" contains "rna-seq". The route order makes smrnaseq win that string; this is
    the other half of the same guard, proving it does not win strings it has no claim on."""
    for assay in (
        "bulk RNA-seq",
        "RNA-seq",
        "mRNA-seq",
        "total RNA-seq transcriptome profiling",
        "poly(A) RNA-seq",
    ):
        assert map_method(assay).pipeline_key == "nf-core/rnaseq", assay


def test_a_single_cell_paper_still_beats_the_small_rna_markers():
    assert map_method("single-cell RNA-seq").pipeline_key == "nf-core/scrnaseq"


# ---- CUT&RUN / CUT&Tag (nf-core/cutandrun) ----


def test_cutandrun_and_cuttag_papers_both_map_to_cutandrun():
    """One pipeline covers both assays. CUT&Tag is a tagmentation variant of CUT&RUN and nf-core
    runs them through the same workflow."""
    for assay in (
        "CUT&RUN",
        "CUT & RUN",
        "CUT-and-RUN",
        "cutandrun",
        "CUT&Tag",
        "CUT and Tag",
        "cuttag",
    ):
        assert map_method(assay).pipeline_key == "nf-core/cutandrun", assay


def test_a_cutandrun_paper_naming_a_histone_mark_does_not_map_to_chipseq():
    """The realistic assay string. CUT&RUN targets the same histone marks ChIP-seq does, so almost
    every one of these papers carries a chipseq marker too, and whichever route is declared first
    wins. Declared below chipseq, every CUT&RUN paper would silently run the wrong pipeline."""
    for assay in (
        "CUT&RUN for H3K27me3",
        "CUT&Tag targeting H3K4me3 in mouse ESCs",
        "CUT&RUN chromatin profiling of histone modifications",
    ):
        assert map_method(assay).pipeline_key == "nf-core/cutandrun", assay


def test_a_chipseq_paper_still_maps_to_chipseq():
    """The other half of that guard: the cutandrun markers must claim only their own assay."""
    for assay in (
        "ChIP-seq",
        "H3K27ac chromatin immunoprecipitation",
        "ChIP-seq of histone modifications",
    ):
        assert map_method(assay).pipeline_key == "nf-core/chipseq", assay


def test_cutandrun_offers_no_finding_set_at_the_gate():
    """cutandrun emits no features x samples matrix: its consensus output is BED-shaped and its
    peak counts are a PEAK_QC step, not a count matrix (conf/modules.config @ 3.2.2). So there is
    no Level-3 route, and the honest state is a gate that offers no finding-set control and says
    why, rather than one that offers a control leading nowhere."""
    from app.services.validation_level3_service import supported_finding_kinds

    assert supported_finding_kinds("nf-core/cutandrun") == []


# ---- microbiome (nf-core/ampliseq) ----


def test_a_microbiome_paper_maps_to_ampliseq():
    """Microbiome work is ordinary lab work and its papers deposit result tables routinely, because
    the pipeline itself performs differential abundance."""
    for assay in (
        "16S rRNA amplicon sequencing",
        "16S rRNA gene sequencing of stool",
        "ITS amplicon sequencing",
        "amplicon sequencing of the V4 region",
        "18S rRNA metabarcoding",
    ):
        assert map_method(assay).pipeline_key == "nf-core/ampliseq", assay


def test_the_microbiome_markers_do_not_claim_shotgun_metagenomics():
    """Shotgun metagenomics is nf-core/mag, a different pipeline with a different output. Amplicon
    markers must name the amplicon, not the field: "metagenomics" belongs to both."""
    assert map_method("shotgun metagenomic sequencing").pipeline_key != "nf-core/ampliseq"


def test_the_microbiome_markers_do_not_claim_bulk_rnaseq():
    """ "16S rRNA" and "18S rRNA" contain "rna". Regression on the whole transcriptomics literature."""
    assert map_method("bulk RNA-seq").pipeline_key == "nf-core/rnaseq"
    assert map_method("RNA-seq of tumour tissue").pipeline_key == "nf-core/rnaseq"
    assert map_method("small RNA-seq").pipeline_key == "nf-core/smrnaseq"


def test_the_common_small_rna_abbreviations_do_not_fall_through_to_bulk():
    """ "sRNAseq" contains "rnaseq". Found while reading a real small-RNA paper (Roy et al. 2023,
    GSE221185), whose own methods call the assay sRNAseq throughout: without this marker the paper
    maps to nf-core/rnaseq, launches a bulk quantification against 20 bp reads, and produces a
    confident answer to a question nobody asked."""
    for assay in ("sRNA-seq", "sRNAseq", "sRNA sequencing", "tsRNA profiling", "piRNA sequencing"):
        assert map_method(assay).pipeline_key == "nf-core/smrnaseq", assay


# ---- routing on the deposited data's own library strategy (plan_4 step 1) ----
#
# A paper is prose and prose is compound: "RRBS and RNA-seq" names two assays and the marker table
# returns the first one it hits. The accession the study was scoped to is not prose. ENA records a
# controlled `library_strategy` per run, and that is a statement about the data itself.


def test_a_bisulfite_accession_routes_to_methylseq():
    route = route_for_library_strategy("Bisulfite-Seq")
    assert route is not None
    assert route.pipeline_key == "nf-core/methylseq"


def test_library_strategy_matching_ignores_punctuation_and_case():
    for spelling in ("Bisulfite-Seq", "BISULFITE-SEQ", "bisulfite seq", " bisulfiteseq "):
        route = route_for_library_strategy(spelling)
        assert route is not None and route.pipeline_key == "nf-core/methylseq", spelling


def test_the_strategies_plan_4_names_all_route():
    expected = {
        "ATAC-seq": "nf-core/atacseq",
        "Bisulfite-Seq": "nf-core/methylseq",
        "ChIP-Seq": "nf-core/chipseq",
        "Hi-C": "nf-core/hic",
        "miRNA-Seq": "nf-core/smrnaseq",
        "AMPLICON": "nf-core/ampliseq",
    }
    for strategy, pipeline_key in expected.items():
        route = route_for_library_strategy(strategy)
        assert route is not None and route.pipeline_key == pipeline_key, strategy


def test_rna_seq_is_declared_but_does_not_route():
    """ENA files bulk, single-cell, total and ribo-depleted RNA under one `RNA-Seq` value. Routing
    on it would send every scRNA-seq study to bulk rnaseq, which is worse than the prose it would be
    overriding. It is still DECLARED, so the guard can tell an RNA pipeline from a wrong one."""
    route = route_for_library_strategy("RNA-Seq")
    assert route is not None
    assert route.pipeline_key is None
    assert "nf-core/scrnaseq" in route.compatible
    assert "nf-core/rnaseq" in route.compatible


def test_an_unknown_strategy_has_no_opinion():
    for strategy in ("OTHER", "WGS", "", None, "Tn-Seq"):
        assert route_for_library_strategy(strategy) is None, strategy


def test_a_chip_strategy_still_admits_cutandrun():
    """ENA has no CUT&RUN value, so CUT&RUN and CUT&Tag runs are deposited as `ChIP-Seq`. The
    marker table puts cutandrun ABOVE chipseq on purpose; the strategy must not undo that."""
    route = route_for_library_strategy("ChIP-Seq")
    assert route is not None
    assert "nf-core/cutandrun" in route.compatible


def test_every_strategy_route_is_compatible_with_its_own_strategy():
    for strategy, route in library_strategy_routes().items():
        if route.pipeline_key is not None:
            assert route.pipeline_key in route.compatible, strategy


# ---- refusing a pipeline the deposited data cannot be fed to (plan_4 step 2) ----
#
# Study 14 reached nf-core/atacseq on Bisulfite-Seq data at `mapping_confidence: exact`, and nothing
# objected. Routing on the deposit (step 1) fixes that where the right pipeline is reachable; this is
# the guard for where it is not, so a wrong-pipeline run cannot be approved at all.


def test_a_pipeline_that_cannot_read_the_deposited_data_is_a_conflict():
    conflict = library_strategy_conflict("nf-core/atacseq", "Bisulfite-Seq")
    assert conflict is not None
    assert "nf-core/atacseq" in conflict
    assert "Bisulfite-Seq" in conflict
    assert is_library_strategy_conflict(conflict)


def test_the_conflict_names_the_pipeline_the_data_does_need():
    conflict = library_strategy_conflict("nf-core/atacseq", "Bisulfite-Seq")
    assert conflict is not None
    assert "nf-core/methylseq" in conflict


def test_a_conflict_on_a_strategy_with_no_route_still_refuses():
    """RNA-Seq is too broad to route on, and it is still enough to know that an ATAC pipeline
    must not read it."""
    conflict = library_strategy_conflict("nf-core/atacseq", "RNA-Seq")
    assert conflict is not None
    assert is_library_strategy_conflict(conflict)


def test_a_pipeline_the_strategy_admits_is_not_a_conflict():
    assert library_strategy_conflict("nf-core/cutandrun", "ChIP-Seq") is None
    assert library_strategy_conflict("nf-core/chipseq", "ChIP-Seq") is None
    assert library_strategy_conflict("nf-core/scrnaseq", "RNA-Seq") is None
    assert library_strategy_conflict("nf-core/methylseq", "Bisulfite-Seq") is None


def test_an_undeclared_strategy_never_contradicts_anything():
    """A strategy nobody has reasoned about must not block a study. Silence is not evidence."""
    for strategy in ("OTHER", "WGS", "Tn-Seq", "", None):
        assert library_strategy_conflict("nf-core/atacseq", strategy) is None, strategy


def test_a_plan_with_no_pipeline_has_nothing_to_contradict():
    assert library_strategy_conflict(None, "Bisulfite-Seq") is None


def test_an_ordinary_blocker_is_not_read_as_a_conflict():
    for blocker in ("no data accession found in the paper", "insufficient method detail to identify an assay", ""):
        assert not is_library_strategy_conflict(blocker), blocker
