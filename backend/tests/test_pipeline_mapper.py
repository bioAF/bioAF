"""Adding an assay to lit_validation is a declared unit of work, not five edits nobody checks.

A new assay touches the mapper's markers, the Level-3 wiring, the QC template map and the
samplesheet generators. Nothing checked that those agree, so a half-wired assay could reach a user
as a silent degrade: the paper maps, the run launches, and the verdict is capped with no statement
of why. These tests are that check, and they run against the declaration table rather than against
a hand-maintained list, so a route added tomorrow is covered the moment it is declared.
"""

from app.services.nf_core_registry_service import QC_TEMPLATE_MAP
from app.services.pipeline_mapper import _ROUTES, map_method
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
