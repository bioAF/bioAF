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
from app.services.pipeline_assay_fallback import resolve_pipeline_for_assay


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
