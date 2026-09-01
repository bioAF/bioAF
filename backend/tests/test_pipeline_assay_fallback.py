"""Any pipeline a lab installs can validate a paper, not only the ones bioAF ships markers for.

The hand-verified marker table reaches four subfields plus the two this branch added. Everything
else in the nf-core catalog ended at `not_reproducible` before any compute, even for a lab that had
already installed exactly the right pipeline and used it every week. This is the fallback: when no
declared route matches, resolve the assay against what this instance actually has, and against the
registry it can install from.

The fallback is deliberately weaker than a declared route. It never overrides one, it never clears
a divergence (`_CLEARED_MAPPING_CONFIDENCE` accepts only `exact`), and it has no `_WIRING` entry,
so `supported_finding_kinds` is empty and the study is Level-2 by construction.
"""

import pytest
import pytest_asyncio

from app.models.nf_core_registry_pipeline import NfCoreRegistryPipeline
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.services.pipeline_assay_fallback import resolve_pipeline_for_assay, split_assay


async def _registry(session, name, description, topics, latest="1.0.0"):
    row = NfCoreRegistryPipeline(
        name=name,
        full_name=f"nf-core/{name}",
        description=description,
        topics=topics,
        releases_json=[{"tag_name": latest}],
        latest_release=latest,
    )
    session.add(row)
    await session.flush()
    return row


async def _installed(session, org_id, name, version="1.0.0"):
    entry = PipelineCatalogEntry(
        organization_id=org_id,
        pipeline_key=f"nf-core/{name}",
        name=f"nf-core/{name}",
        description="",
        source_type="nf-core",
        source_url=f"https://github.com/nf-core/{name}",
        version=version,
        is_builtin=False,
        enabled=True,
    )
    session.add(entry)
    await session.flush()
    return entry


@pytest_asyncio.fixture
async def catalog(session, admin_user):
    """A realistic slice of the nf-core registry: the subfields plan_1 step 4 names."""
    await _registry(
        session,
        "ampliseq",
        "Amplicon sequencing analysis workflow using DADA2 and QIIME2",
        ["16s", "amplicon-sequencing", "its", "metagenomics", "microbiome", "qiime2", "dada2"],
        latest="2.9.0",
    )
    await _registry(
        session,
        "quantms",
        "Quantitative mass spectrometry workflow",
        ["proteomics", "mass-spectrometry", "dia", "dda", "openms"],
        latest="1.3.0",
    )
    await _registry(
        session,
        "riboseq",
        "Analysis of ribosome profiling data",
        ["ribo-seq", "ribosome-profiling", "translation"],
        latest="1.1.0",
    )
    return session


@pytest.mark.asyncio
async def test_a_declared_route_always_wins(session, admin_user, catalog):
    """The fallback is a fallback. A decoy registry row whose description is full of RNA words must
    never displace the hand-verified rnaseq route, which is the only one with a Level-3 wiring."""
    await _registry(session, "decoy", "bulk RNA-seq transcriptome quantification", ["rna-seq", "transcriptomics"])

    mapping = await resolve_pipeline_for_assay(session, admin_user.organization_id, "bulk RNA-seq", tools=[])

    assert mapping.pipeline_key == "nf-core/rnaseq"
    assert mapping.pipeline_version == "3.14.0"


@pytest.mark.asyncio
async def test_a_proteomics_paper_resolves_to_quantms_from_its_topics(session, admin_user, catalog):
    """ "label-free quantitative proteomics" names no pipeline, but it names one of quantms's
    declared topics and shares a word with its description.

    Proteomics has no declared route and will not get one from plan_1: quantms emits long-form
    MSstats input and runs its own differential test, so there is no features x samples matrix for
    the DE notebook. Before the fallback, every one of these papers ended at not_reproducible."""
    mapping = await resolve_pipeline_for_assay(
        session, admin_user.organization_id, "label-free quantitative proteomics", tools=[]
    )

    assert mapping.pipeline_key == "nf-core/quantms"
    assert mapping.pipeline_version == "1.3.0"
    assert mapping.blockers == []


@pytest.mark.asyncio
async def test_the_papers_own_tools_are_part_of_the_match(session, admin_user, catalog):
    """A methods section that names QIIME2 has told us the subfield even when the assay string is
    vague. The tools are already extracted and already stored; using them costs nothing."""
    mapping = await resolve_pipeline_for_assay(
        session, admin_user.organization_id, "microbial community profiling", tools=["QIIME2", "DADA2"]
    )

    assert mapping.pipeline_key == "nf-core/ampliseq"


@pytest.mark.asyncio
async def test_a_fallback_match_is_never_confident_enough_to_clear_a_divergence(session, admin_user, catalog):
    """`_CLEARED_MAPPING_CONFIDENCE` accepts only `exact`. A match made from a description and a
    topic list is a plausible equivalent, not a verified one, and a divergence under it must stay
    attributable to the pipeline substitution."""
    from app.services.validation_classifier_service import _CLEARED_MAPPING_CONFIDENCE

    mapping = await resolve_pipeline_for_assay(
        session, admin_user.organization_id, "label-free quantitative proteomics", tools=[]
    )

    assert mapping.mapping_confidence not in _CLEARED_MAPPING_CONFIDENCE
    assert "nf-core/quantms" in mapping.mapping_notes


@pytest.mark.asyncio
async def test_a_fallback_match_reaches_level_2_only(session, admin_user, catalog):
    """No `_WIRING` entry exists for a pipeline nobody has read the module sources for, so the C1
    gate offers no finding set and says so. That is the honest cap, and it is automatic."""
    from app.services.validation_level3_service import supported_finding_kinds

    mapping = await resolve_pipeline_for_assay(
        session, admin_user.organization_id, "label-free quantitative proteomics", tools=[]
    )

    assert supported_finding_kinds(mapping.pipeline_key) == []


@pytest.mark.asyncio
async def test_an_installed_pipeline_beats_an_equally_good_registry_one(session, admin_user, catalog):
    """What the lab has installed is a statement about what the lab does. Two candidates that the
    text cannot separate are separated by that."""
    await _registry(
        session,
        "quantms2",
        "Quantitative mass spectrometry workflow",
        ["proteomics", "mass-spectrometry", "dia", "dda", "openms"],
    )
    await _installed(session, admin_user.organization_id, "quantms", version="1.3.0")

    mapping = await resolve_pipeline_for_assay(
        session, admin_user.organization_id, "label-free quantitative proteomics", tools=[]
    )

    assert mapping.pipeline_key == "nf-core/quantms"
    # The installed version is what will actually run, so that is what the plan records.
    assert mapping.pipeline_version == "1.3.0"


@pytest.mark.asyncio
async def test_two_candidates_nothing_separates_are_refused_by_name(session, admin_user, catalog):
    """This is a screening tool for papers of unknown validity. Silently picking one of two equally
    plausible pipelines spends real compute on a guess and reports the result as an answer. Refuse,
    and name both so the human can choose."""
    await _registry(
        session,
        "quantms2",
        "Quantitative mass spectrometry workflow",
        ["proteomics", "mass-spectrometry", "dia", "dda", "openms"],
    )

    mapping = await resolve_pipeline_for_assay(
        session, admin_user.organization_id, "label-free quantitative proteomics", tools=[]
    )

    assert mapping.pipeline_key is None
    assert mapping.mapping_confidence == "none"
    assert any("quantms" in b and "quantms2" in b for b in mapping.blockers), mapping.blockers


@pytest.mark.asyncio
async def test_an_assay_nothing_matches_is_still_not_reproducible(session, admin_user, catalog):
    """The fallback widens reach; it does not invent it. An unmatched assay keeps exactly the
    blocker the classifier reads as not_reproducible."""
    mapping = await resolve_pipeline_for_assay(
        session, admin_user.organization_id, "some bespoke ChIP variant", tools=[]
    )

    assert mapping.pipeline_key is None
    assert any("no nf-core equivalent" in b.lower() for b in mapping.blockers)


@pytest.mark.asyncio
async def test_thin_methods_still_read_as_missing_methods_not_unsupported(session, admin_user, catalog):
    """Two different failures with two different remedies. An empty assay must never reach the
    matcher and come back with whichever pipeline scored highest against nothing."""
    for assay in ("", None, "   "):
        mapping = await resolve_pipeline_for_assay(session, admin_user.organization_id, assay, tools=[])
        assert mapping.pipeline_key is None
        assert any("insufficient method detail" in b.lower() for b in mapping.blockers), assay


@pytest.mark.asyncio
async def test_an_archived_pipeline_is_never_offered(session, admin_user):
    """nf-core archives a pipeline when it is superseded or abandoned. Routing a study to one
    spends compute on a workflow the community has stopped maintaining."""
    row = await _registry(session, "hlatyping", "Precision HLA typing", ["hla", "immunology"])
    row.archived = True
    await session.flush()

    mapping = await resolve_pipeline_for_assay(session, admin_user.organization_id, "HLA typing", tools=[])

    assert mapping.pipeline_key is None


@pytest.mark.asyncio
async def test_a_pipeline_with_no_release_is_never_offered(session, admin_user):
    """A route has to pin a version: it is what the catalog installs and what the plan records.
    A registry row with no release cannot pin one."""
    await _registry(session, "riboseq", "Analysis of ribosome profiling data", ["ribo-seq"], latest=None)

    mapping = await resolve_pipeline_for_assay(session, admin_user.organization_id, "ribosome profiling", tools=[])

    assert mapping.pipeline_key is None


@pytest.mark.asyncio
async def test_one_broad_topic_word_is_not_enough_to_route_a_paper(session, admin_user):
    """nf-core topics carry disease and organism words, not just assay words. A paper that merely
    says "cancer" has told us nothing about which pipeline could re-run it, and offering one on that
    basis spends a fetch and a run on a coincidence. Two independent signals, or the pipeline's own
    name, or nothing."""
    await _registry(session, "sarek", "Analysis of germline and somatic variants", ["cancer", "variant-calling"])

    mapping = await resolve_pipeline_for_assay(
        session, admin_user.organization_id, "a bespoke assay in cancer", tools=[]
    )

    assert mapping.pipeline_key is None
    assert any("no nf-core equivalent" in b.lower() for b in mapping.blockers)


@pytest.mark.asyncio
async def test_the_pipelines_own_name_in_the_methods_is_enough_on_its_own(session, admin_user):
    """The opposite end: a methods section that names the pipeline has answered the question
    outright, and needs no second signal."""
    await _registry(session, "methylseq", "Methylation (Bisulfite-Sequencing) analysis", ["methylation"])

    mapping = await resolve_pipeline_for_assay(
        session, admin_user.organization_id, "a bespoke assay", tools=["methylseq"]
    )

    assert mapping.pipeline_key == "nf-core/methylseq"


# ---- the scoped accession's library strategy outranks the paper's prose (plan_4 step 1) ----
#
# The registry fallback above works and nothing could reach it. A real paper is almost never
# single-assay, the extractor emits ONE compound assay string, and `_match_route` returns the first
# marker hit in declaration order, so anything that also mentions RNA-seq routed to rnaseq. Two real
# Bisulfite-Seq studies (GSE213770, GSE228658) were planned against atacseq and rnaseq and declined.


@pytest_asyncio.fixture
async def methyl_registry(session, admin_user):
    await _registry(
        session,
        "methylseq",
        "Methylation (Bisulfite-Sequencing) analysis pipeline",
        ["methylation", "bisulfite-sequencing", "wgbs", "rrbs"],
        latest="2.6.0",
    )
    return session


@pytest.mark.asyncio
async def test_without_a_strategy_a_compound_assay_still_routes_on_its_first_marker(
    session, admin_user, methyl_registry
):
    """The defect this step exists to fix, pinned so the fix cannot be mistaken for a coincidence."""
    mapping = await resolve_pipeline_for_assay(session, admin_user.organization_id, "RRBS and RNA-seq", tools=[])
    assert mapping.pipeline_key == "nf-core/rnaseq"


@pytest.mark.asyncio
async def test_the_deposits_strategy_beats_the_papers_prose(session, admin_user, methyl_registry):
    mapping = await resolve_pipeline_for_assay(
        session,
        admin_user.organization_id,
        "RRBS and RNA-seq",
        tools=[],
        library_strategy="Bisulfite-Seq",
    )

    assert mapping.pipeline_key == "nf-core/methylseq"
    assert mapping.pipeline_version == "2.6.0"
    assert mapping.blockers == []
    assert "Bisulfite-Seq" in mapping.mapping_notes


@pytest.mark.asyncio
async def test_a_strategy_routes_where_the_prose_matched_nothing_at_all(session, admin_user, methyl_registry):
    """The prose route and the fallback both come up empty, and the deposit still knows."""
    mapping = await resolve_pipeline_for_assay(
        session,
        admin_user.organization_id,
        "a bespoke cytosine conversion protocol",
        tools=[],
        library_strategy="Bisulfite-Seq",
    )

    assert mapping.pipeline_key == "nf-core/methylseq"


@pytest.mark.asyncio
async def test_a_strategy_routed_pipeline_takes_its_declared_pin_when_it_has_one(session, admin_user, catalog):
    """Routing on the strategy must not demote a hand-verified route to a registry guess: the plan
    records the pinned version, and the Level-3 wiring is keyed on the pipeline."""
    mapping = await resolve_pipeline_for_assay(
        session,
        admin_user.organization_id,
        "ChIP-seq and RNA-seq",
        tools=[],
        library_strategy="ATAC-seq",
    )

    assert mapping.pipeline_key == "nf-core/atacseq"
    assert mapping.pipeline_version == "2.1.2"


@pytest.mark.asyncio
async def test_a_declared_route_the_strategy_admits_is_left_alone(session, admin_user, catalog):
    """ENA has no CUT&RUN value, so CUT&RUN runs are deposited as ChIP-Seq. The marker table puts
    cutandrun above chipseq deliberately; overriding it here would silently run the wrong pipeline
    on every CUT&RUN paper."""
    mapping = await resolve_pipeline_for_assay(
        session,
        admin_user.organization_id,
        "CUT&RUN for H3K27me3",
        tools=[],
        library_strategy="ChIP-Seq",
    )

    assert mapping.pipeline_key == "nf-core/cutandrun"
    assert mapping.pipeline_version == "3.2.2"


@pytest.mark.asyncio
async def test_a_strategy_too_broad_to_decide_on_never_overrides_the_prose(session, admin_user, catalog):
    """ENA files bulk, single-cell and total RNA under one RNA-Seq value. Routing on it would send
    every scRNA-seq study to bulk rnaseq."""
    mapping = await resolve_pipeline_for_assay(
        session,
        admin_user.organization_id,
        "single-cell RNA-seq of PBMCs",
        tools=[],
        library_strategy="RNA-Seq",
    )

    assert mapping.pipeline_key == "nf-core/scrnaseq"


@pytest.mark.asyncio
async def test_a_strategy_this_instance_cannot_run_leaves_the_prose_route_alone(session, admin_user, catalog):
    """No methylseq in the registry cache and none installed, so there is no version to pin and no
    route to offer. The prose route stands and the C1 guard is what refuses it."""
    mapping = await resolve_pipeline_for_assay(
        session,
        admin_user.organization_id,
        "RRBS and RNA-seq",
        tools=[],
        library_strategy="Bisulfite-Seq",
    )

    assert mapping.pipeline_key == "nf-core/rnaseq"


@pytest.mark.asyncio
async def test_an_undeclared_strategy_has_no_opinion(session, admin_user, catalog):
    for strategy in ("OTHER", "WGS", "", None):
        mapping = await resolve_pipeline_for_assay(
            session, admin_user.organization_id, "bulk RNA-seq", tools=[], library_strategy=strategy
        )
        assert mapping.pipeline_key == "nf-core/rnaseq", strategy


@pytest.mark.asyncio
async def test_a_strategy_route_is_never_exact(session, admin_user, methyl_registry):
    """`exact` is what lets `_attribute` clear a pipeline substitution as an explanation for a
    divergence. A pipeline chosen because the DEPOSIT disagreed with the paper is the last mapping
    that should be able to do that, even when the paper names nf-core."""
    mapping = await resolve_pipeline_for_assay(
        session,
        admin_user.organization_id,
        "RRBS and RNA-seq",
        tools=["nf-core/rnaseq"],
        library_strategy="Bisulfite-Seq",
    )

    assert mapping.pipeline_key == "nf-core/methylseq"
    assert mapping.mapping_confidence == "partial"


@pytest.mark.asyncio
async def test_thin_methods_stay_thin_methods_even_with_a_strategy(session, admin_user, methyl_registry):
    """A paper whose methods cannot be read has a different remedy (read them again) from a paper
    routed to the wrong pipeline, and the classifier keys off that distinction."""
    mapping = await resolve_pipeline_for_assay(
        session, admin_user.organization_id, "", tools=[], library_strategy="Bisulfite-Seq"
    )

    assert mapping.pipeline_key is None
    assert any("insufficient method detail" in b.lower() for b in mapping.blockers)


# ---- a compound assay string is read as the several assays it names (plan_4 step 6) ----
#
# Redundant where an accession is scoped, since the deposit's own strategy is the stronger signal.
# This is for the paper with nothing to key on: study 15's extracted assay was
# 'High-throughput fluorescence imaging (MBD foci), DNA methylation array (EPIC BeadChip), targeted
# RNA-seq, RRBS', four assays in one string, and `_match_route` answered with whichever marker was
# declared first.
#
# The rule is deliberately narrow, because the demo's own studies show what a looser one would
# break: 'bulk RNA-seq and ChIP-seq' (study 4, ran to completion), 'single-cell multiome (scRNA-seq
# + scATAC-seq)' (study 7) and 'bulk RNA-seq and small RNA-seq' (study 9) are all compound and all
# routed correctly by declaration order. A split only overrides that when the fragments AGREE, by
# more than one, on a pipeline the whole string did not choose.


def test_a_single_assay_string_is_not_split():
    assert split_assay("bulk RNA-seq") == ["bulk RNA-seq"]
    assert split_assay("assay for transposase-accessible chromatin") == ["assay for transposase-accessible chromatin"]


def test_the_connectives_papers_write_are_split_on():
    assert split_assay("RRBS and RNA-seq") == ["RRBS", "RNA-seq"]
    assert split_assay("Hi-C, RNA-seq; ATAC-seq") == ["Hi-C", "RNA-seq", "ATAC-seq"]
    assert split_assay("WGBS plus scRNA-seq") == ["WGBS", "scRNA-seq"]
    assert split_assay("ChIP-seq followed by RNA-seq") == ["ChIP-seq", "RNA-seq"]


def test_an_ampersand_is_never_a_separator():
    """CUT&RUN and CUT&Tag are assay NAMES. Splitting on the ampersand turns 'CUT&RUN for H3K27me3'
    into 'CUT' and 'RUN for H3K27me3', and the second half carries the chipseq marker."""
    assert split_assay("CUT&RUN for H3K27me3") == ["CUT&RUN for H3K27me3"]
    assert split_assay("CUT & Tag") == ["CUT & Tag"]


@pytest.mark.asyncio
async def test_the_assay_the_paper_names_most_wins_over_declaration_order(session, admin_user, methyl_registry):
    """Study 15, in one test. Two of its four fragments identify a methylation assay and one
    identifies RNA-seq, and the answer was RNA-seq because that marker is declared first."""
    mapping = await resolve_pipeline_for_assay(
        session,
        admin_user.organization_id,
        "High-throughput fluorescence imaging (MBD foci), whole-genome bisulfite sequencing, "
        "targeted RNA-seq, reduced-representation bisulfite sequencing",
        tools=[],
    )

    assert mapping.pipeline_key == "nf-core/methylseq"
    assert "bisulfite" in mapping.mapping_notes.lower()


@pytest.mark.asyncio
async def test_two_assays_that_disagree_keep_the_declared_order_answer(session, admin_user, catalog):
    """Study 4 and study 9. One fragment each, nothing to prefer, and declaration order is a
    considered ranking rather than an accident. Overriding it on a coin toss would refuse or
    re-route studies that already run correctly."""
    four = await resolve_pipeline_for_assay(session, admin_user.organization_id, "bulk RNA-seq and ChIP-seq", tools=[])
    assert four.pipeline_key == "nf-core/chipseq"

    nine = await resolve_pipeline_for_assay(
        session,
        admin_user.organization_id,
        "bulk RNA-seq and small RNA-seq (exosomal miRNA-seq)",
        tools=[],
    )
    assert nine.pipeline_key == "nf-core/smrnaseq"


@pytest.mark.asyncio
async def test_a_multiome_paper_still_routes_to_single_cell(session, admin_user, catalog):
    """Study 7. A spaced plus IS a separator, and the two halves disagree, so declaration order
    stands and single-cell wins as it did."""
    mapping = await resolve_pipeline_for_assay(
        session, admin_user.organization_id, "single-cell multiome (scRNA-seq + scATAC-seq)", tools=[]
    )

    assert mapping.pipeline_key == "nf-core/scrnaseq"


@pytest.mark.asyncio
async def test_a_cut_and_run_paper_spelled_out_is_not_torn_in_half(session, admin_user, catalog):
    """'cut and run' contains the connective. One fragment resolving is not a compound assay, so
    the whole string's answer stands."""
    mapping = await resolve_pipeline_for_assay(
        session, admin_user.organization_id, "CUT and RUN for H3K27me3", tools=[]
    )

    assert mapping.pipeline_key == "nf-core/cutandrun"


@pytest.mark.asyncio
async def test_the_deposits_strategy_still_outranks_the_vote(session, admin_user, methyl_registry):
    """Prose counting is the weakest of the three signals and must never displace the deposit."""
    mapping = await resolve_pipeline_for_assay(
        session,
        admin_user.organization_id,
        "bisulfite sequencing, RRBS, and targeted RNA-seq",
        tools=[],
        library_strategy="RNA-Seq",
    )

    assert mapping.pipeline_key == "nf-core/rnaseq"


# ---- a registry topic is worth what its rarity says it is worth (plan_5.1 step 4) ----
#
# Every topic scored a flat 3, so two topics that a tenth of the catalog declares (`rna-seq`, `rna`,
# `single-cell`) beat one topic that exactly one pipeline declares (`alternative-splicing`,
# `fusion`, `circrna`). That is backwards: the discriminating evidence is the rare topic, and the
# common ones are what a whole subfield shares.
#
# The weight is inverse document frequency over the registry itself, so it is DATA rather than a
# hand-maintained table of exceptions: a topic's worth follows how many pipelines claim it, and a
# registry refresh re-derives it.


@pytest_asyncio.fixture
async def frequencies(session, admin_user):
    """A registry with known topic frequencies: `broad` is declared by ten pipelines, `narrow` by one.

    Both descriptions share one word with the assay below, which is what puts BOTH candidates over
    `_MIN_SCORE`. Without it the question the test asks ("which of these two wins?") could be
    answered by refusing them both, and it would pass for the wrong reason.
    """
    for index in range(10):
        await _registry(session, f"widely{index}", "hepatocyte workflow", ["broad-signal", "second-broad"])
    await _registry(session, "narrowly", "hepatocyte workflow", ["narrow-signal"])
    return session


@pytest.mark.asyncio
async def test_one_rare_topic_beats_two_common_ones(session, admin_user, frequencies):
    """The whole of the scoring failure, in one assertion.

    Ten pipelines declare `broad-signal` and `second-broad`; one declares `narrow-signal`. A paper
    naming all three is a paper about whatever the rare topic describes. Under flat weighting the
    ten tied with each other at 7 against the specific pipeline's 4, and a tie is refused by name,
    so the paper got no answer at all.
    """
    mapping = await resolve_pipeline_for_assay(
        session,
        admin_user.organization_id,
        "broad signal second broad narrow signal of hepatocyte",
        tools=[],
    )

    assert mapping.pipeline_key == "nf-core/narrowly"


@pytest.mark.asyncio
async def test_a_paper_naming_the_pipeline_still_outranks_any_topic(session, admin_user, frequencies):
    """A paper that names the pipeline has answered the question, and no weighting may overturn
    that. The rarest possible topic is declared by exactly one pipeline, so this is the ceiling
    being held below the name's own score rather than an approximation of it."""
    await _registry(session, "namedpipe", "hepatocyte workflow", ["second-broad"])

    mapping = await resolve_pipeline_for_assay(
        session,
        admin_user.organization_id,
        "narrow signal analysis run with nf-core/namedpipe on hepatocyte",
        tools=[],
    )

    assert mapping.pipeline_key == "nf-core/namedpipe"


@pytest.mark.asyncio
async def test_common_topics_alone_are_not_an_answer(session, admin_user, frequencies):
    """Two topics a tenth of the catalog shares are the subfield, not the pipeline. Answering with
    one of them would be a coin toss between ten equally good candidates, and this is a screening
    tool for papers of unknown validity: it refuses instead."""
    mapping = await resolve_pipeline_for_assay(
        session, admin_user.organization_id, "broad signal and second broad of hepatocyte", tools=[]
    )

    assert mapping.pipeline_key is None


# ---- a contextual match competes instead of pre-empting (plan_5.1 step 3) ----
#
# A declared route short-circuits everything: `_match_route` returns the first marker hit and the
# registry fallback never runs at all. So `rna-seq` did not out-score nf-core/rnafusion, it stopped
# rnafusion from being considered, and the same for rnasplice, dualrnaseq, spatialvi and viralrecon.
# Every one of those has no declared route of its own, so no reordering of the table could reach them.
#
# The contextual match stays a FLOOR rather than becoming a demotion, because a paper that says only
# "RNA sequencing" genuinely carries no evidence separating rnaseq from rnasplice, and rarity cannot
# rank what the paper never mentioned. What changes is that the floor can now be DISPLACED, and only
# by evidence that is actually diagnostic: a topic rarer than anything the floor itself matched.


@pytest_asyncio.fixture
async def rna_family(session, admin_user):
    """The RNA subfield the way the registry declares it: three pipelines sharing the common topics,
    each with one topic of its own, plus enough filler for the frequencies to mean something."""
    await _registry(
        session,
        "rnaseq",
        "RNA sequencing analysis pipeline using STAR, RSEM, HISAT2 or Salmon",
        ["rna", "rna-seq"],
        latest="3.14.0",
    )
    await _registry(
        session,
        "rnasplice",
        "Alternative splicing analysis using RNA-seq",
        ["alternative-splicing", "rna", "rna-seq", "splicing"],
        latest="1.0.4",
    )
    await _registry(
        session,
        "rnafusion",
        "Pipeline for the detection of gene fusions",
        ["fusion", "gene-fusion", "rna", "rna-seq"],
        latest="3.0.2",
    )
    for index in range(8):
        await _registry(session, f"filler{index}", "a workflow", ["rna", "rna-seq"])
    return session


@pytest.mark.asyncio
async def test_a_splicing_paper_reaches_the_splicing_pipeline(session, admin_user, rna_family):
    """The assay says RNA-seq, and `rna-seq` is true of every pipeline in the family. What separates
    them is `alternative-splicing`, which exactly one pipeline in the catalog declares."""
    mapping = await resolve_pipeline_for_assay(
        session, admin_user.organization_id, "RNA-seq alternative splicing analysis", tools=[]
    )

    assert mapping.pipeline_key == "nf-core/rnasplice"


@pytest.mark.asyncio
async def test_a_fusion_paper_reaches_the_fusion_pipeline(session, admin_user, rna_family):
    mapping = await resolve_pipeline_for_assay(
        session, admin_user.organization_id, "RNA-seq for gene fusion detection", tools=[]
    )

    assert mapping.pipeline_key == "nf-core/rnafusion"


@pytest.mark.asyncio
async def test_a_diagnostic_marker_is_never_put_up_for_competition(session, admin_user, rna_family):
    """`bulk rna` identifies the assay on its own, so the registry is never consulted. This is the
    guard on the whole change: bulk RNA-seq is the one assay proven end to end at Level 3, and it is
    the baseline that has held all project."""
    mapping = await resolve_pipeline_for_assay(
        session, admin_user.organization_id, "bulk RNA-seq of liver tissue", tools=[]
    )

    assert mapping.pipeline_key == "nf-core/rnaseq"
    assert mapping.pipeline_version == "3.14.0"


@pytest.mark.asyncio
async def test_a_paper_with_only_contextual_evidence_keeps_the_floor(session, admin_user, rna_family):
    """The measured reason the floor exists. "RNA-seq of primary hepatocytes" carries nothing that
    separates rnaseq from rnasplice or rnafusion, and demoting the marker without a floor made this
    paper a refusal. Rarity cannot rank what the paper never mentioned."""
    mapping = await resolve_pipeline_for_assay(
        session, admin_user.organization_id, "RNA-seq of primary hepatocytes", tools=[]
    )

    assert mapping.pipeline_key == "nf-core/rnaseq"
    assert mapping.pipeline_version == "3.14.0"


@pytest.mark.asyncio
async def test_the_floor_says_that_a_choice_was_made(session, admin_user, rna_family):
    """A scientist reading the plan has to be able to see that the pipeline was weighed rather than
    merely matched, because the whole family was on the table and only prose chose between them."""
    mapping = await resolve_pipeline_for_assay(
        session, admin_user.organization_id, "RNA-seq of primary hepatocytes", tools=[]
    )

    assert mapping.mapping_confidence == "partial"
    assert "rna-seq" in mapping.mapping_notes.lower()
    assert "weighed" in mapping.mapping_notes.lower()


@pytest.mark.asyncio
async def test_a_weaker_candidate_does_not_displace_the_floor(session, admin_user, rna_family):
    """Displacing takes evidence more specific than the floor's own, not merely a higher total. A
    pipeline that wins on description words has not identified the assay, it has shared vocabulary
    with it."""
    await _registry(session, "wordy", "RNA sequencing of primary hepatocytes in liver tissue", [])

    mapping = await resolve_pipeline_for_assay(
        session, admin_user.organization_id, "RNA-seq of primary hepatocytes", tools=[]
    )

    assert mapping.pipeline_key == "nf-core/rnaseq"


@pytest.mark.asyncio
async def test_the_deposit_bounds_what_may_displace_the_floor(session, admin_user, methyl_registry):
    """A strategy with no pipeline of its own still says what must NOT run.

    ENA files bulk, single-cell, total and ribo-depleted RNA under one `RNA-Seq` value, so the
    strategy is declared but deliberately unrouted. It is still a statement about the data: a
    compound paper naming bisulfite work would otherwise be displaced onto methylseq over an RNA-Seq
    deposit, and the C1 gate would refuse the plan it produced.
    """
    mapping = await resolve_pipeline_for_assay(
        session,
        admin_user.organization_id,
        "bisulfite sequencing, RRBS, and targeted RNA-seq",
        tools=[],
        library_strategy="RNA-Seq",
    )

    assert mapping.pipeline_key == "nf-core/rnaseq"


@pytest.mark.asyncio
async def test_the_floors_own_family_words_cannot_be_what_unseats_it(session, admin_user, rna_family):
    """Rarity in the CATALOG is not rarity in a methods section, and this is where they come apart.

    Exactly one pipeline declares `transcriptome`, so inverse document frequency calls it the most
    diagnostic word available. It is nothing of the kind: "total RNA-seq transcriptome profiling" is
    ordinary bulk RNA-seq, and it was measured routing to a de novo assembler. `transcriptom` is
    declared as one of the rnaseq route's own contextual markers, and a word already on that list
    cannot be the evidence that overrules it.
    """
    await _registry(
        session,
        "denovotranscriptreg",
        "Assembly and annotation of transcriptome sequences",
        ["transcriptome", "rna-seq"],
        latest="1.2.0",
    )

    mapping = await resolve_pipeline_for_assay(
        session, admin_user.organization_id, "total RNA-seq transcriptome profiling", tools=[]
    )

    assert mapping.pipeline_key == "nf-core/rnaseq"


# ---- the paper's own tool list is evidence, and whole tokens only (plan_5.1 step 5) ----
#
# `method.tools` is captured by the extractor and carried on the plan, and it was spent on one
# boolean (`_mentions_nf_core`) plus a prose sentence. It is the paper telling you what it did:
# rMATS, Arriba, Bismark, DADA2, Space Ranger. A pipeline that names the same tool in its own
# description or topics is the pipeline that does that work.
#
# WHOLE TOKENS ONLY, which is not a detail. Measured against the live registry, `rmats` matched
# inside "image formats" and `star` matched nf-core/rnaseq's description for a paper that ran
# STAR-Fusion. Substring matching here re-creates exactly the bug this plan exists to fix.


@pytest.mark.asyncio
async def test_a_tool_the_paper_named_can_displace_the_floor(session, admin_user, rna_family):
    """The assay says only "RNA-seq", which is true of the whole family. The tool says which member.

    This is the one signal that separates a fusion paper from a bulk one when the methods section
    describes its assay in family words, which is most of the time.
    """
    await _registry(
        session,
        "fusioncaller",
        "Detection of gene fusions with Arriba and STAR-Fusion",
        ["fusion"],
        latest="3.0.2",
    )

    mapping = await resolve_pipeline_for_assay(session, admin_user.organization_id, "RNA-seq", tools=["Arriba"])

    assert mapping.pipeline_key == "nf-core/fusioncaller"
    assert "arriba" in mapping.mapping_notes.lower()


@pytest.mark.asyncio
async def test_a_tool_the_floors_own_pipeline_uses_keeps_the_floor(session, admin_user, rna_family):
    """The other half of the same evidence. A paper that quantified with Salmon ran bulk RNA-seq,
    and the tool list has to be able to CONFIRM the floor as well as overturn it."""
    await _registry(
        session,
        "fusioncaller",
        "Detection of gene fusions with Arriba and STAR-Fusion",
        ["fusion"],
        latest="3.0.2",
    )

    mapping = await resolve_pipeline_for_assay(session, admin_user.organization_id, "RNA-seq", tools=["Salmon"])

    assert mapping.pipeline_key == "nf-core/rnaseq"


@pytest.mark.asyncio
async def test_a_tool_matches_as_a_whole_token_and_not_inside_a_word(session, admin_user, rna_family):
    """`rmats` appears inside "image formats", which is a real row in the live registry.

    Both candidates here declare the same topic, so the tool list is the only thing separating them.
    Under substring matching both would be credited with the tool, the two would tie, and a tie is
    refused by name: the paper would get no answer at all.
    """
    await _registry(session, "splicecaller", "Isoform switching with rMATS", ["isoform-switching"])
    await _registry(session, "formatter", "Isoform switching across many image formats", ["isoform-switching"])

    mapping = await resolve_pipeline_for_assay(
        session, admin_user.organization_id, "RNA-seq isoform switching", tools=["rMATS"]
    )

    assert mapping.pipeline_key == "nf-core/splicecaller"


@pytest.mark.asyncio
async def test_a_tool_the_whole_catalog_uses_says_nothing(session, admin_user, rna_family):
    """A methods section names every tool it touched, and most of them are plumbing.

    samtools is named by nearly every pipeline there is, so a paper naming it has said nothing about
    which one to run. The same rarity rule that weighs topics weighs tools, and a tool at the floor
    cannot displace a floor.
    """
    for index in range(10):
        await _registry(session, f"plumbing{index}", "A workflow using samtools and fastqc", [])
    await _registry(session, "toolbox", "A workflow using samtools and fastqc", [])

    mapping = await resolve_pipeline_for_assay(
        session, admin_user.organization_id, "RNA-seq", tools=["samtools", "fastqc"]
    )

    assert mapping.pipeline_key == "nf-core/rnaseq"


@pytest.mark.asyncio
async def test_a_pipeline_that_declares_its_plumbing_as_topics_does_not_win_on_it(session, admin_user, rna_family):
    """Measured against the live registry, not imagined. nf-core/hgtseq declares `fastqc`, `multiqc`
    and `samtools` as its own topics, so those words are RARE in the registry while being universal
    in practice, and a paper listing its QC tools was routed to a horizontal-gene-transfer pipeline.
    Frequency in a topic list is not frequency in the world, so the plumbing is named outright."""
    await _registry(
        session, "plumbingtopics", "Detection of horizontal gene transfer", ["fastqc", "multiqc", "samtools"]
    )

    mapping = await resolve_pipeline_for_assay(
        session, admin_user.organization_id, "RNA-seq", tools=["samtools", "fastqc", "MultiQC"]
    )

    assert mapping.pipeline_key == "nf-core/rnaseq"
