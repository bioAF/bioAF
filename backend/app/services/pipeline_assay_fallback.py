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
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Mapping

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
    }
)

# Tools every pipeline in the catalog runs. A methods section lists its plumbing alongside its
# science, and the plumbing says nothing about which pipeline to run: a paper naming samtools has
# named what every workflow here uses to read a BAM.
#
# Rarity cannot catch these on its own, and that was measured rather than assumed. nf-core/hgtseq
# declares `fastqc`, `multiqc` and `samtools` as its own TOPICS, so those words are rare in the
# registry even though they are universal in practice, and a paper listing its QC tools was routed
# to a horizontal-gene-transfer pipeline. Frequency in a topic list is not frequency in the world.
_UBIQUITOUS_TOOLS = frozenset(
    {
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
        "trim galore",
        "seqtk",
        "bwa",
        "bwa mem",
        "bowtie",
        "bowtie2",
        "nextflow",
        "docker",
        "singularity",
        "conda",
        "python",
        "r",
    }
)

# A candidate must clear this to be offered at all, and the threshold is set so that ONE topic hit
# on its own does not. nf-core topics carry disease and organism words ("cancer", "human") as well
# as assay words, so a paper that merely says "cancer" would otherwise be routed to whichever
# pipeline happened to declare that topic. Clearing it takes the pipeline's own name, or two
# independent signals.
_MIN_SCORE = 4

_SCORE_NAME = 4  # the pipeline's own name appears in the paper's text
_SCORE_DESCRIPTION = 1  # a distinctive word shared with the description
_MAX_DESCRIPTION_SCORE = 2

# A tool the paper's methods named, which the candidate names too. Weighted by rarity like a topic,
# because "STAR" is used by half the RNA catalog and "Bismark" is used by one pipeline.
#
# Capped, because a methods section lists every tool it touched. Eight generic ones agreeing is not
# eight times the evidence, and an uncapped sum would let a toolbox description out-argue the assay.
_MAX_TOOL_SCORE = 4.0

# ---- what a declared topic is worth, which is what its RARITY says it is worth ----
#
# Every topic used to score a flat 3, so two topics a tenth of the catalog declares (`rna-seq` by
# 10 of 143, `rna` by 10, `single-cell` by 9) beat one topic that exactly one pipeline declares
# (`alternative-splicing`, `fusion`, `circrna`, `metatranscriptomics`). That is backwards. The rare
# topic is the discriminating evidence; the common ones are what a whole subfield shares.
#
# Inverse document frequency over the registry itself, so the weighting is DATA and not a
# hand-maintained table of exceptions: a registry refresh re-derives every weight.
#
# The denominator is NOT the catalog size. No topic is declared by anything near 143 pipelines, so
# log(143/count) would spend its whole range on distinctions that never occur and compress the ones
# that do. `_COMMON_TOPIC_SHARE` is where "common" starts (~7% of the catalog, which is 10 of 143,
# which is exactly `rna-seq`), and the scale runs from there up to a topic nobody else declares.
# A frequency counted over a handful of pipelines is noise, not evidence: on a registry of four,
# "two of them declare it" would read as common when it means nothing at all. Below this many, the
# catalog is too small for its own frequencies and the scale is held at a fixed width.
_COMMON_TOPIC_SHARE = 0.07
_COMMON_TOPIC_MINIMUM = 8
_TOPIC_WEIGHT_CEILING = 3.9  # declared by exactly one pipeline
_TOPIC_WEIGHT_FLOOR = 1.5  # declared by `_COMMON_TOPIC_SHARE` of the catalog or more

# Two properties hold this calibration together, and changing any constant above has to keep both:
#
#   ceiling < _SCORE_NAME       a paper that names the pipeline has answered the question, and no
#                               topic may overturn that.
#   ceiling > 2 * floor         one diagnostic topic outweighs two generic ones, which is the whole
#                               point. It also keeps `_MIN_SCORE` meaning what its comment says:
#                               one topic alone, however rare, is still not enough.


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
    """
    folded = _phrase(haystack)
    return [t for t in candidate.topics if (p := _phrase(t)) and marker_matches(p, folded)]


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


def _candidate_text(candidate: _Candidate) -> str:
    """Everything the registry says about a pipeline, folded: its name, its topics, its description."""
    return _phrase(" ".join([candidate.name, *candidate.topics, candidate.description]))


def _tool_phrases(tools: list[str] | None) -> list[str]:
    """The paper's tool names, folded and kept WHOLE.

    Never split. "STAR-Fusion" folds to "star fusion" and stays one phrase, because splitting it
    yields "star", which nf-core/rnaseq's own description names, and the fusion paper would score
    the bulk pipeline. That is the same defect as matching `transcriptom` inside
    `metatranscriptomics`, one field along.
    """
    seen: list[str] = []
    for tool in tools or []:
        phrase = _phrase(tool)
        if len(phrase) > 2 and phrase not in _STOPWORDS and phrase not in _UBIQUITOUS_TOOLS and phrase not in seen:
            seen.append(phrase)
    return seen


def _named_tools(candidate: _Candidate, tool_phrases: list[str]) -> list[str]:
    """Which of the paper's tools this candidate names, matched as whole tokens at both ends.

    The trailing anchor is what the leading one alone does not give: `rmats` starts a word inside
    nothing, but a paper naming "STAR" would otherwise be credited to every pipeline whose
    description says "STARsolo". The leading anchor is what keeps `rmats` out of "image formats".
    """
    text = _candidate_text(candidate)
    return [t for t in tool_phrases if re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", text)]


def _weights_by_frequency(counts: Counter, population: int) -> dict[str, float]:
    """Inverse document frequency over ``population`` candidates, bounded to the topic scale."""
    common = max(_COMMON_TOPIC_MINIMUM, round(population * _COMMON_TOPIC_SHARE))
    span = math.log(common)
    return {
        key: _TOPIC_WEIGHT_FLOOR
        + (_TOPIC_WEIGHT_CEILING - _TOPIC_WEIGHT_FLOOR) * max(0.0, min(1.0, math.log(common / count) / span))
        for key, count in counts.items()
    }


def _tool_weights(candidates: list[_Candidate], tool_phrases: list[str]) -> dict[str, float]:
    """What each of the paper's tools is worth, from how many pipelines name it.

    Same rarity rule as a topic, and for the same reason: half the RNA catalog names STAR and one
    pipeline names Bismark, so the two cannot count the same.
    """
    if not tool_phrases:
        return {}
    counts = Counter()
    for candidate in candidates:
        for tool in _named_tools(candidate, tool_phrases):
            counts[tool] += 1
    return _weights_by_frequency(counts, len(candidates))


def _topic_weights(candidates: list[_Candidate]) -> dict[str, float]:
    """How much each declared topic is worth, from how many pipelines declare it.

    Counted once per resolution over the same candidate list the scoring runs against, so the
    weights and the candidates can never be out of step with each other.
    """
    counts = Counter(topic for candidate in candidates for topic in set(candidate.topics))
    return _weights_by_frequency(counts, len(candidates))


def _score(
    candidate: _Candidate,
    haystack: str,
    haystack_tokens: set[str],
    weights: Mapping[str, float],
    tool_phrases: list[str] | None = None,
    tool_weights: Mapping[str, float] | None = None,
) -> tuple[float, list[str]]:
    """How well ``candidate`` explains the paper's text, and which fields said so.

    Rounded, because the topic weights are floats and two candidates matching different topics of
    the same rarity have to compare EQUAL: a tie is refused by name, and a tie that float error
    turned into a hairline win would be answered instead.
    """
    score = 0.0
    why: list[str] = []

    # The name has to start a word too: "rnaseq" appears inside "scrnaseq" and inside "dualrnaseq",
    # and scoring the bulk pipeline 4 on a single-cell paper is the same defect one layer down.
    if candidate.name in haystack_tokens or marker_matches(candidate.name, haystack):
        score += _SCORE_NAME
        why.append(f"the paper names {candidate.name}")

    matched_topics = _matched_topics(candidate, haystack)
    if matched_topics:
        score += sum(weights.get(t, _TOPIC_WEIGHT_CEILING) for t in matched_topics)
        rare = sorted(matched_topics, key=lambda t: -weights.get(t, _TOPIC_WEIGHT_CEILING))
        why.append("declared topics " + ", ".join(rare))

    shared = _tokens(candidate.description) & haystack_tokens
    if shared:
        score += min(_SCORE_DESCRIPTION * len(shared), _MAX_DESCRIPTION_SCORE)
        why.append("description terms " + ", ".join(sorted(shared)))

    # A tool already credited as a topic is not two pieces of evidence wearing two hats.
    named = [t for t in _named_tools(candidate, tool_phrases or []) if t not in {_phrase(m) for m in matched_topics}]
    if named:
        score += min(sum((tool_weights or {}).get(t, _TOPIC_WEIGHT_CEILING) for t in named), _MAX_TOOL_SCORE)
        why.append("the paper's own tools " + ", ".join(sorted(named)))

    return round(score, 3), why


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


def _fallback_match(
    candidates: list[_Candidate], haystack: str, tool_phrases: list[str] | None = None
) -> tuple[_Candidate, list[str]] | None:
    """The single best-scoring candidate for ``haystack``, or None when nothing clears the bar or
    two candidates tie. Shared by the whole-string match and the per-fragment vote so both agree."""
    haystack_tokens = _tokens(haystack)
    weights = _topic_weights(candidates)
    tool_weights = _tool_weights(candidates, tool_phrases or [])
    scored = [
        (score, candidate, why)
        for candidate in candidates
        if (result := _score(candidate, haystack, haystack_tokens, weights, tool_phrases, tool_weights))
        and (score := result[0]) >= _MIN_SCORE
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
    candidates: list[_Candidate],
    haystack: str,
    assay: str | None,
    declared: PipelineMapping,
    tool_phrases: list[str] | None = None,
) -> PipelineMapping:
    """Either nothing scored, which leaves the unchanged blocker, or two candidates tied, which is
    refused BY NAME. This is a screening tool for papers of unknown validity: silently picking one of
    two equally plausible pipelines spends real compute on a guess and reports the result as an
    answer."""
    haystack_tokens = _tokens(haystack)
    weights = _topic_weights(candidates)
    tool_weights = _tool_weights(candidates, tool_phrases or [])
    scored = [
        (result[0], candidate)
        for candidate in candidates
        if (result := _score(candidate, haystack, haystack_tokens, weights, tool_phrases, tool_weights))[0]
        >= _MIN_SCORE
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


def _displaces_floor(
    best: _Candidate,
    best_score: float,
    floor_key: str,
    floor_score: float,
    family: tuple[str, ...],
    haystack: str,
    tool_phrases: list[str],
    tool_weights: Mapping[str, float],
) -> bool:
    """Whether ``best`` has earned the right to overrule a contextual match.

    Two things have to be true, and the second is what stops this from being a re-run of the bug it
    replaces. A higher total is not enough on its own: a candidate can out-total the floor on
    description words and on the very family words that put the floor there, and sharing vocabulary
    with a paper is not the same as identifying its assay.

    So ``best`` must also declare a topic the paper wrote that the floor's own family vocabulary does
    not cover. `fusion`, `alternative-splicing`, `artic` and `spatial` are each such a word, and a
    paper writing one has said something `rna-seq` and `amplicon sequencing` cannot say. A paper
    that writes only "RNA sequencing" has not, and the floor is the honest answer for it.
    """
    if best.pipeline_key == floor_key or best_score <= floor_score:
        return False
    if _diagnostic_topics(_matched_topics(best, haystack), family):
        return True
    # A tool the paper itself named is the paper saying what it did, which is at least as specific
    # as a topic somebody wrote on a pipeline's README. It has to be a DISCRIMINATING tool, though,
    # by the same rarity rule the topics follow: samtools and fastqc are named by nearly every
    # pipeline there is, and a paper listing its plumbing has not said which pipeline to run.
    return any(
        tool_weights.get(t, _TOPIC_WEIGHT_CEILING) > _TOPIC_WEIGHT_FLOOR for t in _named_tools(best, tool_phrases)
    )


def _offer_over_floor(
    candidates: list[_Candidate],
    haystack: str,
    assay: str | None,
    floor: PipelineMapping,
    strategy_route: LibraryStrategyRoute | None,
    family: tuple[str, ...],
    tool_phrases: list[str],
) -> PipelineMapping | None:
    """What wins when a contextual route has to defend itself, or None to leave the floor untouched.

    Returns the floor's own mapping re-stated (so the plan records that a choice was made and what
    it was weighed against) or the candidate that displaced it. Never a refusal: the floor answered
    this paper before and must keep answering it.

    Only pipelines the scoped deposit ADMITS may compete. A strategy that is declared but
    deliberately unrouted (ENA files bulk, single-cell, total and ribo-depleted RNA under one
    `RNA-Seq` value, so no one pipeline can be named for it) still says plenty about what must not
    run: a compound paper reading "bisulfite sequencing, RRBS, and targeted RNA-seq" over an RNA-Seq
    deposit would otherwise be displaced onto methylseq, which the C1 gate would then refuse outright.
    Weights are counted over the same admissible set, so rarity means "rare among the pipelines that
    could actually run this data".
    """
    floor_key = floor.pipeline_key
    assert floor_key is not None  # only called with a declared route in hand

    if strategy_route is not None:
        candidates = [c for c in candidates if c.pipeline_key in strategy_route.compatible]
        if not candidates:
            return _floor_stands(floor, assay, floor_key, weighed_against=None)

    weights = _topic_weights(candidates)
    tool_weights = _tool_weights(candidates, tool_phrases)
    tokens = _tokens(haystack)

    def score_of(candidate: _Candidate) -> float:
        return _score(candidate, haystack, tokens, weights, tool_phrases, tool_weights)[0]

    floor_candidate = next((c for c in candidates if c.pipeline_key == floor_key), None)
    floor_score = score_of(floor_candidate) if floor_candidate else 0.0

    match = _fallback_match(candidates, haystack, tool_phrases)
    if match is None:
        return _floor_stands(floor, assay, floor_key, weighed_against=None)

    best, why = match
    if not _displaces_floor(best, score_of(best), floor_key, floor_score, family, haystack, tool_phrases, tool_weights):
        return _floor_stands(floor, assay, floor_key, weighed_against=best.pipeline_key)

    logger.info(
        "org-independent: assay %r displaced the contextual floor %s -> %s", assay, floor_key, best.pipeline_key
    )
    where = "installed on this bioAF" if best.installed else "available from the nf-core registry"
    return PipelineMapping(
        pipeline_key=best.pipeline_key,
        pipeline_version=best.version,
        # Never `exact`. A match made from a topic list is a plausible equivalent, not a verified
        # one, so `_attribute` must keep a pipeline substitution on the table as an explanation for
        # any divergence.
        mapping_confidence="partial",
        mapping_notes=(
            f"The paper's assay ('{assay}') names {floor_key} only in the sense that its whole "
            f"subfield does. Weighed against every pipeline this bioAF can run, "
            f"{best.pipeline_key} {best.version} ({where}) is the more specific answer, on "
            f"{'; '.join(why)}. bioAF has not verified what this pipeline emits, so the study is "
            "limited to QC-level evidence and cannot reproduce the paper's finding set."
        ),
    )


def _floor_stands(
    floor: PipelineMapping, assay: str | None, floor_key: str, weighed_against: str | None
) -> PipelineMapping:
    """The contextual route, re-stated as the considered answer it now is.

    The confidence is `partial` rather than `exact` even where the paper names nf-core, because the
    evidence that chose this pipeline was true of its whole subfield. A scientist reading the plan
    should see that the family was on the table and that prose is all that chose within it.
    """
    runner_up = (
        f" The nearest alternative the registry offered was {weighed_against}, which carries no "
        "evidence more specific than that."
        if weighed_against and weighed_against != floor_key
        else " Nothing else in the registry cleared the bar."
    )
    return PipelineMapping(
        pipeline_key=floor.pipeline_key,
        pipeline_version=floor.pipeline_version,
        mapping_confidence="partial",
        mapping_notes=(
            f"The paper's assay ('{assay}') identifies a family rather than a pipeline: the words it "
            f"uses are as true of its neighbours as of {floor_key}. It was weighed against every "
            f"pipeline this bioAF can run and {floor_key} stands as the answer of last resort for "
            f"that family.{runner_up} Scoping this study to the accession it should reproduce is "
            "what settles it outright, because a deposit's declared library strategy is not prose."
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
    if declared.pipeline_key is not None and evidence is not None and not evidence.diagnostic:
        offered = _offer_over_floor(
            candidates,
            haystack,
            assay,
            declared,
            strategy_route,
            evidence.route.contextual_markers,
            tool_phrases,
        )
        if offered is not None:
            return offered

    if declared.pipeline_key is not None:
        return declared

    match = _fallback_match(candidates, haystack, tool_phrases)
    if match is None:
        return _no_single_fallback(candidates, haystack, assay, declared, tool_phrases)

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
