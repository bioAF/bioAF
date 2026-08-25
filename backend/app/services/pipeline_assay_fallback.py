"""Resolve a paper's assay against the pipelines this instance actually has (lit_validation B3).

``pipeline_mapper`` maps a handful of assays that someone has verified end to end by reading the
pipeline's own module sources. That table is deliberately narrow and deliberately slow to grow. The
nf-core catalog is neither: a lab can install any of ~120 pipelines, and before this every paper
outside the declared table ended at ``not_reproducible`` before any compute, even when the lab had
already installed exactly the right pipeline and used it every week.

So: declared route first, always. When none matches, score the assay (and the paper's own named
tools) against what the org has installed and what the registry cache knows about, and offer the
winner. The offer is weaker than a route in three ways, all structural rather than advisory:

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
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.nf_core_registry_pipeline import NfCoreRegistryPipeline
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.services.pipeline_mapper import PipelineMapping, map_method

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


def _phrase(topic: str) -> str:
    """A registry topic as it would be written in prose: ``amplicon-sequencing`` -> ``amplicon sequencing``."""
    return re.sub(r"[^a-z0-9]+", " ", (topic or "").lower()).strip()


def _score(candidate: _Candidate, haystack: str, haystack_tokens: set[str]) -> tuple[int, list[str]]:
    """How well ``candidate`` explains the paper's text, and which fields said so."""
    score = 0
    why: list[str] = []

    if candidate.name in haystack_tokens or candidate.name in haystack:
        score += _SCORE_NAME
        why.append(f"the paper names {candidate.name}")

    matched_topics = [t for t in candidate.topics if (p := _phrase(t)) and p in haystack]
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


async def resolve_pipeline_for_assay(
    session: AsyncSession,
    org_id: int,
    assay: str | None,
    tools: list[str] | None = None,
    reference_build: str | None = None,
) -> PipelineMapping:
    """``map_method``, widened to everything this instance can run.

    Returns the declared route when one matches. Otherwise the best-scoring installable pipeline,
    or the unchanged ``not_reproducible`` / ``missing_methods`` blocker when nothing does.
    """
    tools = tools or []
    declared = map_method(assay, tools, reference_build)
    if declared.pipeline_key is not None:
        return declared

    # An assay too thin to identify is a different failure with a different remedy, and it must not
    # reach the matcher: with nothing to match on, "no candidate scored" would be reported as an
    # unsupported assay rather than as a paper whose methods need reading again.
    if not (assay or "").strip():
        return declared

    haystack = " ".join([assay or "", *[t or "" for t in tools]]).lower()
    haystack_tokens = _tokens(haystack)

    scored: list[tuple[int, _Candidate, list[str]]] = []
    for candidate in await _candidates(session, org_id):
        score, why = _score(candidate, haystack, haystack_tokens)
        if score >= _MIN_SCORE:
            scored.append((score, candidate, why))

    if not scored:
        return declared

    # Installed beats not-installed at equal score: what a lab has installed is a statement about
    # what that lab does. Ties break on the pipeline key so a rerun resolves identically.
    scored.sort(key=lambda item: (item[0], item[1].installed, item[1].pipeline_key), reverse=True)
    best_score, best, why = scored[0]

    rivals = [c for s, c, _ in scored[1:] if s == best_score and c.installed == best.installed]
    if rivals:
        names = ", ".join(sorted([best.pipeline_key, *[c.pipeline_key for c in rivals]]))
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

    logger.info("org %d: assay %r resolved to %s by fallback (score %d)", org_id, assay, best.pipeline_key, best_score)
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
