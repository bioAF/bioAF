"""Resolve a paper's assay against the pipelines this instance actually has (lit_validation B3).

``pipeline_mapper`` maps a handful of assays that someone has verified end to end by reading the
pipeline's own module sources. That table is deliberately narrow and deliberately slow to grow. The
nf-core catalog is neither: a lab can install any of ~120 pipelines, and before this every paper
outside the declared table ended at ``not_reproducible`` before any compute, even when the lab had
already installed exactly the right pipeline and used it every week.

So: declared route first, corrected by the deposit and then by the paper's own emphasis. A paper
is prose and prose is compound, so "RRBS and RNA-seq" routes on whichever marker is declared first.
Two things outrank that reading, in order of how much they know:

* the accession the study was scoped to is not prose, so where its own ``library_strategy``
  contradicts the prose route, the data wins;
* where there is no such accession, the compound string is split and each fragment resolved on its
  own, and a pipeline more than one fragment names beats the one the first marker chose.

When no route matches at all, score the assay (and the paper's own named tools) against what the org
has installed and what the registry cache knows about, and offer the winner. The offer is weaker than a route in three ways, all structural rather than advisory:

* its ``mapping_confidence`` is never ``exact``, so ``_attribute`` cannot clear our side of a
  divergence and a pipeline substitution stays a live explanation;
* it has no ``_WIRING`` entry, so ``supported_finding_kinds`` is empty and the study is capped at
  Level-2 by construction rather than by a rule someone has to remember;
* two candidates nothing separates are refused by name rather than picked.

Matching is deterministic and auditable, no model in the loop: the same paper resolves the same way
twice, and ``mapping_notes`` says which fields matched.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.nf_core_registry_pipeline import NfCoreRegistryPipeline
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.services.pipeline_mapper import (
    LibraryStrategyRoute,
    PipelineMapping,
    declared_route_version,
    map_method,
    marker_matches,
    match_route,
    route_for_library_strategy,
)

logger = logging.getLogger("bioaf.pipeline_assay_fallback")

# Words that appear in nearly every assay string and nearly every pipeline description. Matching on
# them would score every candidate equally and turn the tie rule into the only thing that ever
# fires.
#
# The tail of this set is the plumbing every nf-core pipeline runs. A methods section lists its
# plumbing alongside its science, and a paper naming samtools has named what every workflow here
# uses to read a BAM. They are here rather than in a list of their own because this is already the
# list of words that do not discriminate, and because `_tool_phrases` and `_matched_topics` both
# consult it: nf-core/hgtseq declares `fastqc`, `multiqc` and `samtools` as its own TOPICS, and a
# paper listing its QC tools was routed to a horizontal-gene-transfer pipeline on them.
_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "using",
        "used",
        "into",
        "over",
        "per",
        "via",
        "seq",
        "sequencing",
        "sequence",
        "sequenced",
        "analysis",
        "analyses",
        "workflow",
        "pipeline",
        "data",
        "dataset",
        "datasets",
        "profiling",
        "profile",
        "profiles",
        "study",
        "experiment",
        "experiments",
        "sample",
        "samples",
        "library",
        "libraries",
        "reads",
        "read",
        "raw",
        "quality",
        "control",
        "processing",
        "based",
        "high",
        "throughput",
        "next",
        "generation",
        "best",
        "practice",
        "practices",
        "genomics",
        "bioinformatics",
        "quantification",
        # the plumbing
        "samtools",
        "bcftools",
        "htslib",
        "bedtools",
        "picard",
        "fastqc",
        "multiqc",
        "fastp",
        "trimmomatic",
        "cutadapt",
        "seqtk",
        "bwa",
        "bowtie",
        "bowtie2",
        "nextflow",
    }
)

# A candidate must clear this to be offered at all, and the threshold is set so that ONE topic hit
# on its own does not. nf-core topics carry disease and organism words ("cancer", "human") as well
# as assay words, so a paper that merely says "cancer" would otherwise be routed to whichever
# pipeline happened to declare that topic. Clearing it takes the pipeline's own name, or two
# independent signals.
_MIN_SCORE = 4

_SCORE_NAME = 4  # the pipeline's own name appears in the paper's text
_SCORE_TOPIC = 3  # a declared topic appears as a phrase
_SCORE_DESCRIPTION = 1  # a distinctive word shared with the description
_MAX_DESCRIPTION_SCORE = 2


@dataclass(frozen=True)
class _Candidate:
    pipeline_key: str
    name: str
    version: str
    description: str
    topics: tuple[str, ...]
    installed: bool


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if len(t) > 2 and t not in _STOPWORDS}


def _phrase(text: str) -> str:
    """Punctuation folded to single spaces: ``amplicon-sequencing`` -> ``amplicon sequencing``.

    Applied to BOTH sides. Folding only the topic was measured to under-credit the very pipeline a
    paper is about: nf-core/rnaseq declares `rna-seq`, which folds to "rna seq", and a paper writing
    "total RNA-seq" does not contain that string. The floor then scored almost nothing and was
    displaced by whatever shared a description word with it.
    """
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _matched_topics(candidate: _Candidate, haystack: str) -> list[str]:
    """Every topic ``candidate`` declares that the paper's text actually writes.

    A topic has to START a word, the same rule the assay markers follow. Plain containment matches
    `rna seq` inside `scrna seq`, which is how a single-cell paper scored the bulk pipeline's topics.

    A topic that is a `_STOPWORDS` entry is skipped, which is what stops a pipeline declaring its own
    plumbing (`fastqc`, `multiqc`, `samtools`) from winning a paper that merely listed its QC tools.

    Two SPELLINGS of one word count once. nf-core/bactmap declares both `bacteria` and `bacterial`,
    which are one word to any paper that writes either, and counting each put a mapping-and-phylogeny
    pipeline above nf-core/bacass on a bacterial ASSEMBLY paper.

    Deliberately narrow: only a single word that begins another single word, never a phrase. A
    general topic and a compound built from it (`spatial` beside `spatial-transcriptomics`, `rna`
    beside `rna-seq`) are two real claims, and merging those was measured to reward a pipeline for
    declaring two vague topics over one that declares the exact one.
    """
    folded = _phrase(haystack)
    matched = [t for t in candidate.topics if (p := _phrase(t)) and p not in _STOPWORDS and marker_matches(p, folded)]

    def is_spelling_of(topic: str, other: str) -> bool:
        a, b = _phrase(topic), _phrase(other)
        return " " not in a and " " not in b and a != b and b.startswith(a)

    return [t for t in matched if not any(o is not t and is_spelling_of(t, o) for o in matched)]


def _diagnostic_topics(topics: list[str], family: tuple[str, ...]) -> list[str]:
    """The topics in ``topics`` that say something the floor's own family vocabulary does not.

    ``family`` is the floor route's declared contextual markers, which is exactly the list of words
    that are true of the whole subfield. A topic those words already cover is not evidence against
    the floor: nf-core/denovotranscript declares `transcriptome` and `rna-seq`, and "total RNA-seq
    transcriptome profiling" is ordinary bulk RNA-seq no matter how few pipelines declare
    `transcriptome`. Rarity in the CATALOG is not the same as rarity in a methods section, and this
    is where the two come apart.
    """
    folded_family = [_phrase(marker) for marker in family]
    return [t for t in topics if not any(marker_matches(m, _phrase(t)) for m in folded_family)]


def _tool_phrases(tools: list[str] | None) -> list[str]:
    """The paper's tool names, folded, deduplicated, and with the plumbing dropped.

    What is dropped is the point: `_STOPWORDS` carries samtools, fastqc and multiqc, so they never
    reach the haystack. A paper naming them has named what every workflow here runs, and
    nf-core/hgtseq declares all three as its own TOPICS, so a paper listing its QC tools was routed
    to a horizontal-gene-transfer pipeline.
    """
    seen: list[str] = []
    for tool in tools or []:
        phrase = _phrase(tool)
        if len(phrase) > 2 and phrase not in _STOPWORDS and phrase not in seen:
            seen.append(phrase)
    return seen


def _score(candidate: _Candidate, haystack: str, haystack_tokens: set[str]) -> tuple[int, list[str]]:
    """How well ``candidate`` explains the paper's text, and which fields said so.

    The paper's own tool list is part of ``haystack``, so a tool reaches this through the topic and
    description matching that is already here. A separate tool signal was measured against the live
    registry and moved no answer: nf-core descriptions name Salmon, Bismark and DADA2, but not
    Arriba, rMATS, STAR-Fusion or Space Ranger, so there is little for it to match on.
    """
    score = 0
    why: list[str] = []

    # The name has to start a word too: "rnaseq" appears inside "scrnaseq" and inside "dualrnaseq",
    # and scoring the bulk pipeline 4 on a single-cell paper is the same defect one layer down.
    if candidate.name in haystack_tokens or marker_matches(candidate.name, haystack):
        score += _SCORE_NAME
        why.append(f"the paper names {candidate.name}")

    matched_topics = _matched_topics(candidate, haystack)
    if matched_topics:
        score += _SCORE_TOPIC * len(matched_topics)
        why.append("declared topics " + ", ".join(sorted(matched_topics)))

    shared = _tokens(candidate.description) & haystack_tokens
    if shared:
        score += min(_SCORE_DESCRIPTION * len(shared), _MAX_DESCRIPTION_SCORE)
        why.append("description terms " + ", ".join(sorted(shared)))

    return score, why


async def _candidates(session: AsyncSession, org_id: int) -> list[_Candidate]:
    """Every nf-core pipeline this org could run, installed ones carrying their installed version."""
    installed_rows = (
        (
            await session.execute(
                select(PipelineCatalogEntry).where(
                    PipelineCatalogEntry.organization_id == org_id,
                    PipelineCatalogEntry.enabled.is_(True),
                    PipelineCatalogEntry.source_type == "nf-core",
                )
            )
        )
        .scalars()
        .all()
    )
    installed_versions = {row.pipeline_key: row.version for row in installed_rows if row.pipeline_key}

    registry_rows = (
        (await session.execute(select(NfCoreRegistryPipeline).where(NfCoreRegistryPipeline.archived.is_(False))))
        .scalars()
        .all()
    )

    out: list[_Candidate] = []
    seen: set[str] = set()
    for row in registry_rows:
        pipeline_key = row.full_name or f"nf-core/{row.name}"
        # A route has to pin a version: it is what the catalog installs and what the plan records.
        version = installed_versions.get(pipeline_key) or row.latest_release
        if not version:
            continue
        seen.add(pipeline_key)
        out.append(
            _Candidate(
                pipeline_key=pipeline_key,
                name=(row.name or "").lower(),
                version=version,
                description=row.description or "",
                topics=tuple(str(t) for t in (row.topics or [])),
                installed=pipeline_key in installed_versions,
            )
        )

    # An installed pipeline the registry cache has never heard of (installed before a refresh, or
    # refreshed away) is still runnable here, so it stays a candidate on its name alone.
    for row in installed_rows:
        if row.pipeline_key and row.pipeline_key not in seen and row.version:
            out.append(
                _Candidate(
                    pipeline_key=row.pipeline_key,
                    name=row.pipeline_key.split("/")[-1].lower(),
                    version=row.version,
                    description=row.description or "",
                    topics=(),
                    installed=True,
                )
            )
    return out


# What a methods section writes between two assay names. Deliberately short, and two obvious
# separators are deliberately missing:
#
#   `&`  CUT&RUN and CUT&Tag are assay NAMES. Splitting on the ampersand turns "CUT&RUN for H3K27me3"
#        into "CUT" and "RUN for H3K27me3", and the second half carries the chipseq marker `h3k`.
#   `/`  papers write "10x/Chromium" and "Salmon/Alevin" as single names at least as often as they
#        write it between two assays.
#
# " and " is here despite "cut and run" containing it, because the two-fragment rule below is what
# makes that safe: "cut" identifies nothing, so the split yields one assay, not two.
_ASSAY_SEPARATORS = re.compile(
    r"\s*(?:,|;|\+|\band\b|\bplus\b|\bas well as\b|\bcombined with\b|\bfollowed by\b)\s*",
    re.IGNORECASE,
)


def split_assay(assay: str | None) -> list[str]:
    """The pieces of a compound methods string, or the whole string when it names one assay."""
    parts = [p.strip() for p in _ASSAY_SEPARATORS.split(assay or "") if p and p.strip()]
    return parts or ([assay.strip()] if (assay or "").strip() else [])


def _fallback_match(candidates: list[_Candidate], haystack: str) -> tuple[_Candidate, list[str]] | None:
    """The single best-scoring candidate for ``haystack``, or None when nothing clears the bar or
    two candidates tie. Shared by the whole-string match and the per-fragment vote so both agree."""
    haystack_tokens = _tokens(haystack)
    scored = [
        (score, candidate, why)
        for candidate in candidates
        if (result := _score(candidate, haystack, haystack_tokens)) and (score := result[0]) >= _MIN_SCORE
        for why in [result[1]]
    ]
    if not scored:
        return None
    # Installed beats not-installed at equal score: what a lab has installed is a statement about
    # what that lab does. Ties break on the pipeline key so a rerun resolves identically.
    scored.sort(key=lambda item: (item[0], item[1].installed, item[1].pipeline_key), reverse=True)
    best_score, best, why = scored[0]
    if [c for s, c, _ in scored[1:] if s == best_score and c.installed == best.installed]:
        return None
    return best, why


def _fragment_pipeline(candidates: list[_Candidate], fragment: str) -> str | None:
    """Which pipeline one fragment of a compound assay identifies, on its own, or None."""
    route = map_method(fragment, [], None)
    if route.pipeline_key is not None:
        return route.pipeline_key
    match = _fallback_match(candidates, fragment.lower())
    return match[0].pipeline_key if match else None


def _majority_assay(candidates: list[_Candidate], assay: str, declared_key: str | None) -> tuple[str, list[str]] | None:
    """The pipeline a compound assay string names MORE THAN ONCE, when one does and it is not the
    answer declaration order already gave. Otherwise None, and today's answer stands.

    Narrow on purpose. `_match_route` returns the first marker hit in DECLARATION order, which is a
    considered ranking rather than an accident: "bulk RNA-seq and ChIP-seq" resolves to chipseq and
    that is right. So a split only overrides it when the fragments AGREE, by more than one, on
    something else. Two fragments pointing two ways is a real ambiguity that counting cannot settle,
    and the ranking is a better tie-break than a coin toss.
    """
    fragments = split_assay(assay)
    if len(fragments) < 2:
        return None
    named = [(f, key) for f in fragments if (key := _fragment_pipeline(candidates, f))]
    # One fragment identifying anything is not a compound assay: it is one assay written beside prose
    # the mapper cannot read ("CUT and RUN for H3K27me3").
    if len(named) < 2:
        return None
    counts = Counter(key for _f, key in named)
    winner, votes = counts.most_common(1)[0]
    if votes < 2 or list(counts.values()).count(votes) > 1 or winner == declared_key:
        return None
    return winner, [f for f, key in named if key == winner]


async def _offer_for_strategy(
    session: AsyncSession,
    org_id: int,
    route: LibraryStrategyRoute,
    strategy: str,
    assay: str | None,
    displaced: PipelineMapping,
) -> PipelineMapping | None:
    """Route on what the deposit declares it is, or None when this instance cannot run that pipeline.

    Returning None matters as much as returning a mapping: with no version to pin there is no route
    to offer, and inventing one would put an uninstallable pipeline into an approved plan. The prose
    route then stands and the C1 guard is what refuses it, which is a refusal a human can act on.
    """
    pipeline_key = route.pipeline_key
    assert pipeline_key is not None  # callers check; a strategy with no route never gets here

    version = declared_route_version(pipeline_key)
    where = "a hand-verified route"
    if version is None:
        candidate = next((c for c in await _candidates(session, org_id) if c.pipeline_key == pipeline_key), None)
        if candidate is None:
            logger.info(
                "org %d: deposit declares %r but %s is neither installed nor in the registry cache",
                org_id,
                strategy,
                pipeline_key,
            )
            return None
        version = candidate.version
        where = "installed on this bioAF" if candidate.installed else "available from the nf-core registry"

    logger.info(
        "org %d: library_strategy %r routed to %s (over %s)", org_id, strategy, pipeline_key, displaced.pipeline_key
    )
    would_have = displaced.pipeline_key or "no pipeline at all"
    return PipelineMapping(
        pipeline_key=pipeline_key,
        pipeline_version=version,
        # Never `exact`. `exact` is what lets `_attribute` clear a pipeline substitution as an
        # explanation for a divergence, and a pipeline chosen BECAUSE the deposit contradicted the
        # paper is the last mapping that should be able to do that.
        mapping_confidence="partial",
        mapping_notes=(
            f"The accession this study was scoped to is deposited as '{strategy}', so that is what "
            f"the data actually is. The paper's own words ('{assay}') would have selected "
            f"{would_have}, which does not consume {strategy} data. Routed to {pipeline_key} "
            f"{version} ({where}) on the deposit's own declaration rather than on the paper's prose."
        ),
    )


async def _offer_by_key(
    session: AsyncSession, org_id: int, candidates: list[_Candidate], pipeline_key: str
) -> tuple[str, str] | None:
    """``(pipeline_key, version)`` for a pipeline this instance can run, or None when it cannot.

    The declared pin wins where there is one: it is what the catalog installs, what the Level-3
    wiring was verified against, and what a rerun reproduces.
    """
    version = declared_route_version(pipeline_key)
    if version is not None:
        return pipeline_key, version
    candidate = next((c for c in candidates if c.pipeline_key == pipeline_key), None)
    return (pipeline_key, candidate.version) if candidate else None


def _no_single_fallback(
    candidates: list[_Candidate], haystack: str, assay: str | None, declared: PipelineMapping
) -> PipelineMapping:
    """Either nothing scored, which leaves the unchanged blocker, or two candidates tied, which is
    refused BY NAME. This is a screening tool for papers of unknown validity: silently picking one of
    two equally plausible pipelines spends real compute on a guess and reports the result as an
    answer."""
    haystack_tokens = _tokens(haystack)
    scored = [
        (result[0], candidate)
        for candidate in candidates
        if (result := _score(candidate, haystack, haystack_tokens))[0] >= _MIN_SCORE
    ]
    if not scored:
        return declared
    top = max(s for s, _ in scored)
    installed = any(c.installed for s, c in scored if s == top)
    names = ", ".join(sorted({c.pipeline_key for s, c in scored if s == top and c.installed == installed}))
    return PipelineMapping(
        pipeline_key=None,
        pipeline_version=None,
        mapping_confidence="none",
        mapping_notes=(
            f"Assay '{assay}' matches more than one installable pipeline equally well ({names}). "
            "Choosing one would spend compute on a guess, so none was selected."
        ),
        blockers=[f"more than one nf-core pipeline matches this assay equally well: {names}"],
    )


def _offer_over_floor(
    candidates: list[_Candidate],
    haystack: str,
    assay: str | None,
    floor: PipelineMapping,
    strategy_route: LibraryStrategyRoute | None,
    family: tuple[str, ...],
) -> PipelineMapping:
    """What wins when a contextual route has to defend itself: the floor re-stated, or its displacer.

    Never a refusal. The floor answered this paper before the registry was consulted and must keep
    answering it, because a paper writing only "RNA sequencing" carries nothing that separates
    nf-core/rnaseq from nf-core/rnasplice, and no scorer can rank what was never mentioned.

    Displacing takes two things. A higher total is not enough on its own: a candidate can out-total
    the floor on description words and on the very family words that put the floor there. It must
    also declare a topic the paper wrote that the floor's own family vocabulary does not cover.
    `fusion`, `alternative-splicing`, `artic` and `spatial` are each such a word; `transcriptome`
    beside a bulk RNA-seq floor is not, however few pipelines declare it.

    Only pipelines the scoped deposit ADMITS may compete. A strategy that is declared but
    deliberately unrouted (ENA files bulk, single-cell, total and ribo-depleted RNA under one
    `RNA-Seq` value) still says what must not run: a compound paper reading "bisulfite sequencing,
    RRBS, and targeted RNA-seq" over an RNA-Seq deposit would otherwise be displaced onto methylseq,
    which the C1 gate would then refuse outright.
    """
    floor_key = floor.pipeline_key
    assert floor_key is not None  # only called with a declared route in hand

    if strategy_route is not None:
        candidates = [c for c in candidates if c.pipeline_key in strategy_route.compatible]

    match = _fallback_match(candidates, haystack) if candidates else None
    if match is not None:
        best, why = match
        tokens = _tokens(haystack)
        floor_candidate = next((c for c in candidates if c.pipeline_key == floor_key), None)
        floor_score = _score(floor_candidate, haystack, tokens)[0] if floor_candidate else 0
        beats_floor = best.pipeline_key != floor_key and _score(best, haystack, tokens)[0] > floor_score
        if beats_floor and _diagnostic_topics(_matched_topics(best, haystack), family):
            logger.info("assay %r displaced the contextual floor %s -> %s", assay, floor_key, best.pipeline_key)
            where = "installed on this bioAF" if best.installed else "available from the nf-core registry"
            return PipelineMapping(
                pipeline_key=best.pipeline_key,
                pipeline_version=best.version,
                # Never `exact`. A match made from a topic list is a plausible equivalent, not a
                # verified one, so `_attribute` must keep a pipeline substitution on the table as an
                # explanation for any divergence.
                mapping_confidence="partial",
                mapping_notes=(
                    f"The paper's assay ('{assay}') names {floor_key} only in the sense that its "
                    f"whole subfield does. Weighed against every pipeline this bioAF can run, "
                    f"{best.pipeline_key} {best.version} ({where}) is the more specific answer, on "
                    f"{'; '.join(why)}. bioAF has not verified what this pipeline emits, so the "
                    "study is limited to QC-level evidence and cannot reproduce the paper's "
                    "finding set."
                ),
            )

    # The floor stands, re-stated as the considered answer it now is. The route's OWN confidence is
    # carried through: weighing a floor and finding nothing better says nothing about how good the
    # route was, and `exact` (the paper's own methods name nf-core) is the only value `_attribute`
    # accepts to clear a pipeline substitution as the explanation for a divergence.
    runner_up = match[0].pipeline_key if match else None
    weighed = (
        f" The nearest alternative the registry offered was {runner_up}, which carries no evidence "
        "more specific than that."
        if runner_up and runner_up != floor_key
        else " Nothing else in the registry cleared the bar."
    )
    return PipelineMapping(
        pipeline_key=floor.pipeline_key,
        pipeline_version=floor.pipeline_version,
        mapping_confidence=floor.mapping_confidence,
        mapping_notes=(
            f"The paper's assay ('{assay}') identifies a family rather than a pipeline: the words it "
            f"uses are as true of its neighbours as of {floor_key}. It was weighed against every "
            f"pipeline this bioAF can run and {floor_key} stands as the answer of last resort for "
            f"that family.{weighed} Scoping this study to the accession it should reproduce is what "
            "settles it outright, because a deposit's declared library strategy is not prose."
        ),
        blockers=list(floor.blockers),
    )


async def resolve_pipeline_for_assay(
    session: AsyncSession,
    org_id: int,
    assay: str | None,
    tools: list[str] | None = None,
    reference_build: str | None = None,
    library_strategy: str | None = None,
) -> PipelineMapping:
    """``map_method``, widened to everything this instance can run, and corrected by the deposit.

    Returns the declared route when one matches and the scoped accession's own ``library_strategy``
    does not contradict it. Otherwise the pipeline that strategy names, then the best-scoring
    installable pipeline, then the unchanged ``not_reproducible`` / ``missing_methods`` blocker.
    """
    tools = tools or []
    declared = map_method(assay, tools, reference_build)

    # An assay too thin to identify is a different failure with a different remedy, and it must not
    # reach any matcher: with nothing to match on, "no candidate scored" would be reported as an
    # unsupported assay rather than as a paper whose methods need reading again. A deposit's
    # strategy does not rescue it either, because a paper nobody can read carries no claim to
    # reproduce.
    if not (assay or "").strip():
        return declared

    # The paper is prose and prose is compound: "RRBS and RNA-seq" names two assays and the marker
    # table returns whichever is declared first. The accession the study was scoped to is not prose,
    # so where the two disagree the deposit wins -- but only where they genuinely disagree. A prose
    # route the strategy admits (cutandrun under ChIP-Seq, scrnaseq under RNA-Seq) is the more
    # specific answer and stands.
    strategy_route = route_for_library_strategy(library_strategy)
    if (
        strategy_route is not None
        and strategy_route.pipeline_key is not None
        and declared.pipeline_key not in strategy_route.compatible
    ):
        offered = await _offer_for_strategy(
            session, org_id, strategy_route, str(library_strategy).strip(), assay, declared
        )
        if offered is not None:
            return offered

    candidates = await _candidates(session, org_id)

    # A paper is one string naming as many assays as it ran. Where its own fragments agree, by more
    # than one, on a pipeline that declaration order did not choose, that agreement is the better
    # reading of the paper than whichever marker happens to be declared first.
    majority = _majority_assay(candidates, assay or "", declared.pipeline_key)
    if majority is not None:
        winner, saying_so = majority
        pinned = await _offer_by_key(session, org_id, candidates, winner)
        if pinned is not None:
            pinned_key, pinned_version = pinned
            named = ", ".join(f"'{f}'" for f in saying_so)
            return PipelineMapping(
                pipeline_key=pinned_key,
                pipeline_version=pinned_version,
                mapping_confidence="partial",
                mapping_notes=(
                    f"The paper's methods name several assays ('{assay}'). More of them identify "
                    f"{pinned_key} than anything else ({named}), so that is what was planned rather "
                    f"than {declared.pipeline_key or 'no pipeline'}, which is what the first "
                    "recognized assay name alone would have selected. Reading prose is weaker than "
                    "reading the deposit: scoping this study to the accession it should reproduce "
                    "settles it outright."
                ),
            )

    # The paper's own tools are part of what is matched against, but only the ones that mean
    # something: plumbing in the haystack is plumbing scored as a declared topic.
    tool_phrases = _tool_phrases(tools)
    haystack = " ".join([(assay or "").lower(), *tool_phrases])

    # A declared route that was chosen by CONTEXTUAL evidence is a floor, not an answer. `rna-seq`
    # is as true of gene fusion, alternative splicing and dual host-pathogen work as it is of bulk
    # RNA-seq, and returning on it stopped every one of those pipelines from being considered at
    # all. So the registry gets to compete, and the floor stands unless something more specific than
    # it wins.
    #
    # Diagnostic routes never reach here: they are the paper identifying its own assay, and the
    # registry has nothing to add to that.
    evidence = match_route((assay or "").lower())
    if declared.pipeline_key is not None and evidence is not None and not evidence[1]:
        return _offer_over_floor(candidates, haystack, assay, declared, strategy_route, evidence[0].contextual_markers)

    if declared.pipeline_key is not None:
        return declared

    match = _fallback_match(candidates, haystack)
    if match is None:
        return _no_single_fallback(candidates, haystack, assay, declared)

    best, why = match
    logger.info("org %d: assay %r resolved to %s by fallback", org_id, assay, best.pipeline_key)
    where = "installed on this bioAF" if best.installed else "available from the nf-core registry"
    return PipelineMapping(
        pipeline_key=best.pipeline_key,
        pipeline_version=best.version,
        # Never `exact`: a match made from a description and a topic list is a plausible equivalent,
        # not a verified one, so `_attribute` must keep a pipeline substitution on the table as an
        # explanation for any divergence.
        mapping_confidence="partial",
        mapping_notes=(
            f"No hand-verified route covers assay '{assay}', so it was matched against the pipelines "
            f"this bioAF can run: {best.pipeline_key} {best.version} ({where}), on {'; '.join(why)}. "
            "bioAF has not verified what this pipeline emits, so the study is limited to QC-level "
            "evidence and cannot reproduce the paper's finding set."
        ),
    )
