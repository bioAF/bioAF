"""Lit Review Run: on-demand LLM-driven paper recommendation pipeline.

The flow per SPEC-literature-lit-review-run.md:
  1. Build a compact experiment context block.
  2. Ask the org's active LLM to generate up to 5 expansion queries.
  3. Run each query across all enabled sources (in-process via the search
     adapter machinery), dedupe candidates, drop library + dismissed.
  4. Ask the LLM to score the remaining candidates 0.0-1.0 with one-line
     reasoning.
  5. Persist the top N (above score_threshold) as pending
     literature_recommendations, creating Paper rows on the fly with
     provenance=lit_review_run.
  6. Emit LITERATURE_REVIEW_RUN_COMPLETED.

LLM calls go through the existing provider client abstraction
(`app.services.llm_provider_clients.get_client`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.database as _database
from app.models.experiment import Experiment
from app.models.literature import (
    EXTRACTION_NONE,
    LiteratureAssociation,
    LiteraturePaper,
    LiteraturePaperDismissal,
    LiteratureRecommendation,
    LiteratureReviewRun,
    LiteratureSourcesConfig,
    PROVENANCE_LIT_REVIEW_RUN,
    REC_PENDING,
    SCOPE_EXPERIMENT,
    SCOPE_PROJECT,
    SEARCH_COMPLETE,
    SEARCH_FAILED,
    SEARCH_PARTIAL,
    SEARCH_RUNNING,
    derive_bucket,
)
from app.services import audit_service, llm_provider_config_service
from app.services.event_bus import event_bus
from app.services.event_types import (
    LITERATURE_REVIEW_RUN_COMPLETED,
    LITERATURE_REVIEW_RUN_FAILED,
)
from app.services.literature import paper_service
from app.services.literature.sources import PaperRecord, get_adapter
from app.services.llm_provider_clients import ProviderError, get_client

logger = logging.getLogger("bioaf.literature.lit_review_run_service")


_MAX_EXPANSION_QUERIES = 5
_DEFAULT_CANDIDATE_CAP = 50
_PER_SOURCE_TIMEOUT_SECONDS = 180


class ReviewRunFailed(Exception):
    pass


class NoActiveLlmProvider(Exception):
    pass


async def create_run(
    session: AsyncSession,
    *,
    org_id: int,
    experiment_id: int,
    triggered_by_user_id: int,
    max_recommendations: int = 10,
    score_threshold: float = 0.33,
    api_key_id: int | None = None,
) -> LiteratureReviewRun:
    """Insert a new LiteratureReviewRun row and audit log entry. The caller
    commits and then calls schedule_run()."""
    cfg = await llm_provider_config_service.get_active(session, org_id)
    if cfg is None:
        raise NoActiveLlmProvider("org has no active LLM provider")

    rs = await session.execute(select(Experiment).where(Experiment.id == experiment_id))
    experiment = rs.scalar_one_or_none()
    if experiment is None or experiment.organization_id != org_id:
        raise ReviewRunFailed("experiment not found in this org")

    run = LiteratureReviewRun(
        organization_id=org_id,
        experiment_id=experiment_id,
        triggered_by_user_id=triggered_by_user_id,
        status="queued",
        llm_provider=cfg.provider,
        llm_model=cfg.model or "",
        max_recommendations=max_recommendations,
        score_threshold=score_threshold,
    )
    session.add(run)
    await session.flush()
    await audit_service.log_action(
        session,
        user_id=triggered_by_user_id,
        api_key_id=api_key_id,
        entity_type="literature_review_run",
        entity_id=run.id,
        action="create",
        details={
            "experiment_id": experiment_id,
            "llm_provider": cfg.provider,
            "llm_model": cfg.model,
        },
    )
    return run


async def schedule_run(*, run_id: int) -> None:
    asyncio.create_task(_execute_run(run_id))


async def _execute_run(run_id: int) -> None:
    factory = _database.async_session_factory
    if factory is None:
        return
    try:
        async with factory() as s:  # type: ignore[misc]
            run = await _load_run(s, run_id)
            if run is None:
                return
            run.status = SEARCH_RUNNING
            run.started_at = datetime.now(UTC)
            await s.commit()

        await _do_run(run_id)
    except Exception as e:  # pragma: no cover
        logger.exception("Lit Review Run %s failed", run_id)
        async with factory() as s:  # type: ignore[misc]
            run = await _load_run(s, run_id)
            if run is not None:
                run.status = SEARCH_FAILED
                run.error_message = str(e)[:500]
                run.completed_at = datetime.now(UTC)
                await s.commit()
        await event_bus.emit(LITERATURE_REVIEW_RUN_FAILED, {"run_id": run_id})


async def _do_run(run_id: int) -> None:
    factory = _database.async_session_factory
    assert factory is not None

    async with factory() as s:  # type: ignore[misc]
        run = await _load_run(s, run_id)
        if run is None:
            return
        experiment = (
            await s.execute(select(Experiment).where(Experiment.id == run.experiment_id))
        ).scalar_one_or_none()
        if experiment is None:
            run.status = SEARCH_FAILED
            run.error_message = "experiment not found"
            run.completed_at = datetime.now(UTC)
            await s.commit()
            return

        context_block = await _build_experiment_context(s, run, experiment)
        provider_cfg = await llm_provider_config_service.get_for_provider(
            s, run.organization_id, run.llm_provider
        )
        if provider_cfg is None:
            run.status = SEARCH_FAILED
            run.error_message = "llm provider configuration disappeared"
            run.completed_at = datetime.now(UTC)
            await s.commit()
            return
        api_key = provider_cfg.api_key

        sources_cfg = (
            await s.execute(
                select(LiteratureSourcesConfig).where(
                    LiteratureSourcesConfig.organization_id == run.organization_id,
                    LiteratureSourcesConfig.enabled == True,  # noqa: E712
                )
            )
        ).scalars().all()
        sources_by_name = {row.source: row for row in sources_cfg}

    # 1. LLM generates expansion queries.
    queries: list[str] = []
    try:
        queries = await _llm_generate_queries(
            provider=run.llm_provider,
            model=run.llm_model,
            api_key=api_key,
            context_block=context_block,
        )
    except ProviderError as e:
        await _mark_failed(run_id, f"llm_query_generation: {e}")
        return

    if not queries:
        await _mark_failed(run_id, "llm_generated_no_valid_queries")
        return

    async with factory() as s:  # type: ignore[misc]
        run = await _load_run(s, run_id)
        run.expansion_queries_json = queries
        await s.commit()

    # 2. Fan out across sources for each query, aggregate, dedupe.
    candidates: list[PaperRecord] = []
    failed_sources = 0
    succeeded_sources = 0
    seen_keys: set[str] = set()
    for q in queries:
        per_query_tasks = {
            src: asyncio.create_task(
                _safe_source_search(src, q, sources_by_name[src].api_key)
            )
            for src in sources_by_name
        }
        for src, t in per_query_tasks.items():
            try:
                records = await t
                succeeded_sources += 1
            except Exception as e:  # pragma: no cover
                logger.warning("source %s failed during query '%s': %s", src, q, e)
                failed_sources += 1
                continue
            for rec in records:
                key = (rec.doi or "").lower() or rec.title.lower()
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                candidates.append(rec)

    # 3. Drop library and dismissed.
    candidates = await _exclude_library_and_dismissed(run_id, candidates)

    if not candidates:
        async with factory() as s:  # type: ignore[misc]
            run = await _load_run(s, run_id)
            run.candidate_count = 0
            run.recommendation_count = 0
            run.status = SEARCH_COMPLETE
            run.completed_at = datetime.now(UTC)
            await s.commit()
        await event_bus.emit(
            LITERATURE_REVIEW_RUN_COMPLETED,
            {"run_id": run_id, "recommendation_count": 0},
        )
        return

    candidates = candidates[:_DEFAULT_CANDIDATE_CAP]

    # 4. LLM scoring pass.
    try:
        scored = await _llm_score_candidates(
            provider=run.llm_provider,
            model=run.llm_model,
            api_key=api_key,
            context_block=context_block,
            candidates=candidates,
        )
    except ProviderError as e:
        await _mark_failed(run_id, f"llm_ranking: {e}")
        return

    # 5. Persist recommendations.
    async with factory() as s:  # type: ignore[misc]
        run = await _load_run(s, run_id)
        run.candidate_count = len(candidates)

        # Sort by score desc, keep those above threshold, cap at max_recommendations.
        scored_sorted = sorted(scored, key=lambda x: x[1], reverse=True)
        kept = [tup for tup in scored_sorted if tup[1] >= run.score_threshold][
            : run.max_recommendations
        ]
        rec_count = 0
        for candidate, score, reasoning in kept:
            paper_id = await _upsert_lit_review_paper(
                s, org_id=run.organization_id, user_id=run.triggered_by_user_id, rec=candidate
            )
            existing = (
                await s.execute(
                    select(LiteratureRecommendation).where(
                        LiteratureRecommendation.organization_id == run.organization_id,
                        LiteratureRecommendation.paper_id == paper_id,
                        LiteratureRecommendation.experiment_id == run.experiment_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                continue
            s.add(
                LiteratureRecommendation(
                    organization_id=run.organization_id,
                    paper_id=paper_id,
                    experiment_id=run.experiment_id,
                    review_run_id=run.id,
                    relevance_score=score,
                    relevance_bucket=derive_bucket(score),
                    reasoning=reasoning,
                    status=REC_PENDING,
                )
            )
            rec_count += 1

        run.recommendation_count = rec_count
        if failed_sources and not succeeded_sources:
            run.status = SEARCH_FAILED
            run.error_message = "all sources failed"
        elif failed_sources:
            run.status = SEARCH_PARTIAL
        else:
            run.status = SEARCH_COMPLETE
        run.completed_at = datetime.now(UTC)

        await audit_service.log_action(
            s,
            user_id=run.triggered_by_user_id,
            entity_type="literature_review_run",
            entity_id=run.id,
            action="complete",
            details={
                "candidate_count": run.candidate_count,
                "recommendation_count": rec_count,
                "status": run.status,
                "failed_sources": failed_sources,
            },
        )
        await s.commit()

    await event_bus.emit(
        LITERATURE_REVIEW_RUN_COMPLETED,
        {"run_id": run_id, "recommendation_count": rec_count},
    )


async def _safe_source_search(src: str, query: str, api_key: str | None) -> list[PaperRecord]:
    adapter = get_adapter(src)
    return await asyncio.wait_for(
        adapter.search(query=query, max_results=20, api_key=api_key),
        timeout=_PER_SOURCE_TIMEOUT_SECONDS,
    )


async def _build_experiment_context(
    session: AsyncSession, run: LiteratureReviewRun, experiment: Experiment
) -> str:
    """Return a Markdown context block summarizing the experiment plus up to
    30 papers already associated with it."""
    parts: list[str] = []
    parts.append(f"Experiment: {experiment.name}")
    if getattr(experiment, "description", None):
        parts.append(f"Description: {experiment.description}")
    if getattr(experiment, "organism", None):
        parts.append(f"Organism: {experiment.organism}")
    if getattr(experiment, "tissue_type", None):
        parts.append(f"Tissue: {experiment.tissue_type}")

    # Library context: papers already associated with this experiment.
    rs = await session.execute(
        select(LiteraturePaper)
        .join(LiteratureAssociation, LiteratureAssociation.paper_id == LiteraturePaper.id)
        .where(
            LiteratureAssociation.scope_type == SCOPE_EXPERIMENT,
            LiteratureAssociation.scope_id == run.experiment_id,
            LiteratureAssociation.removed_at.is_(None),
        )
        .limit(30)
    )
    library = rs.scalars().all()
    if library:
        parts.append("\nAlready-known papers:")
        for p in library:
            parts.append(f"- {p.title} (doi: {p.doi or 'n/a'})")

    # Dismissed DOIs we want the LLM not to suggest.
    rs = await session.execute(
        select(LiteraturePaper.doi)
        .join(
            LiteraturePaperDismissal,
            LiteraturePaperDismissal.paper_id == LiteraturePaper.id,
        )
        .where(
            LiteraturePaperDismissal.organization_id == run.organization_id,
            LiteraturePaperDismissal.reversed_at.is_(None),
        )
    )
    dismissed_dois = [d for (d,) in rs.fetchall() if d]
    if dismissed_dois:
        parts.append("\nPapers already dismissed (do not suggest):")
        for d in dismissed_dois[:50]:
            parts.append(f"- {d}")

    return "\n".join(parts)


async def _llm_generate_queries(
    provider: str, model: str, api_key: str | None, context_block: str
) -> list[str]:
    system = (
        "You are a research librarian assisting a computational biology team. "
        "Generate exactly five concise scientific search queries (one per line, "
        "no numbering, no punctuation other than spaces and hyphens) that "
        "explore adjacent terms, synonyms, and related concepts for the given "
        "experiment context. Avoid duplicates and avoid generic terms."
    )
    payload = f"Experiment context:\n\n{context_block}\n\nReturn five queries, one per line."
    client = get_client(provider)
    output = await client.submit(prompt=system, payload=payload, model=model, api_key=api_key)
    lines: list[str] = []
    for raw in (output or "").splitlines():
        stripped = raw.strip(" -*\t")
        # Strip leading numbering like "1." or "1)"
        stripped = re.sub(r"^\d+[.)]\s*", "", stripped)
        if not stripped:
            continue
        if len(stripped) > 200:
            continue
        lines.append(stripped)
        if len(lines) >= _MAX_EXPANSION_QUERIES:
            break
    return lines


async def _llm_score_candidates(
    provider: str,
    model: str,
    api_key: str | None,
    context_block: str,
    candidates: list[PaperRecord],
) -> list[tuple[PaperRecord, float, str]]:
    """Ask the LLM to score each candidate paper 0.0-1.0 with a one-line reason.

    Returns a list of (candidate, score, reasoning) tuples in the original
    candidate order. Candidates the LLM omits are dropped."""
    summaries = []
    for i, c in enumerate(candidates):
        first_author = ""
        if c.authors:
            f = c.authors[0]
            first_author = f"{f.get('family','')}".strip()
        title = c.title.replace("\n", " ")[:300]
        abstract = (c.abstract or "")[:600]
        summaries.append(
            f"[{i}] {title} | first_author={first_author} | doi={c.doi or 'n/a'}\nAbstract: {abstract}"
        )
    candidate_block = "\n\n".join(summaries)

    system = (
        "You are a research librarian. Score each candidate paper 0.0 to 1.0 on "
        "relevance to the experiment context. Respond with valid JSON only: a "
        "list of objects {\"index\": int, \"score\": float, \"reasoning\": str}. "
        "Use one short sentence for reasoning. Do not include any text outside "
        "the JSON. Omit clearly irrelevant candidates."
    )
    payload = (
        f"Experiment context:\n\n{context_block}\n\n"
        f"Candidate papers:\n\n{candidate_block}\n\n"
        "Return a JSON array. Example: [{\"index\": 0, \"score\": 0.82, \"reasoning\": \"...\"}]"
    )
    client = get_client(provider)
    output = await client.submit(prompt=system, payload=payload, model=model, api_key=api_key)
    parsed = _parse_scoring_response(output)
    results: list[tuple[PaperRecord, float, str]] = []
    seen_indices: set[int] = set()
    for entry in parsed:
        try:
            idx = int(entry["index"])
            score = float(entry["score"])
        except (KeyError, ValueError, TypeError):
            continue
        if idx in seen_indices or idx < 0 or idx >= len(candidates):
            continue
        seen_indices.add(idx)
        score = max(0.0, min(1.0, score))
        reasoning = str(entry.get("reasoning") or "")[:500]
        results.append((candidates[idx], score, reasoning))
    return results


def _parse_scoring_response(text: str) -> list[dict[str, Any]]:
    if not text:
        return []
    text = text.strip()
    # Try direct JSON parse first.
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass
    # Strip markdown fences.
    m = re.search(r"```(?:json)?\s*(.+?)```", text, flags=re.DOTALL)
    if m:
        try:
            parsed = json.loads(m.group(1))
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
    # Find the first JSON array in the text.
    m = re.search(r"\[\s*\{.*\}\s*\]", text, flags=re.DOTALL)
    if m:
        try:
            parsed = json.loads(m.group(0))
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
    return []


async def _exclude_library_and_dismissed(
    run_id: int, candidates: list[PaperRecord]
) -> list[PaperRecord]:
    """Drop candidates that already exist in the org's literature library
    (any provenance) or that are actively dismissed."""
    if not candidates:
        return []
    factory = _database.async_session_factory
    assert factory is not None
    async with factory() as s:  # type: ignore[misc]
        run = await _load_run(s, run_id)
        if run is None:
            return []
        dois = [c.doi for c in candidates if c.doi]
        if not dois:
            return candidates
        rs = await s.execute(
            select(LiteraturePaper.doi).where(
                LiteraturePaper.organization_id == run.organization_id,
                LiteraturePaper.doi.in_(dois),
            )
        )
        known = {d for (d,) in rs.fetchall() if d}
        rs2 = await s.execute(
            select(LiteraturePaper.doi)
            .join(
                LiteraturePaperDismissal,
                LiteraturePaperDismissal.paper_id == LiteraturePaper.id,
            )
            .where(
                LiteraturePaperDismissal.organization_id == run.organization_id,
                LiteraturePaperDismissal.reversed_at.is_(None),
                LiteraturePaper.doi.in_(dois),
            )
        )
        dismissed = {d for (d,) in rs2.fetchall() if d}
    return [c for c in candidates if c.doi not in known and c.doi not in dismissed]


async def _upsert_lit_review_paper(
    session: AsyncSession,
    *,
    org_id: int,
    user_id: int,
    rec: PaperRecord,
) -> int:
    existing = await paper_service.find_duplicate(
        session, org_id=org_id, doi=rec.doi, title=rec.title, authors=rec.authors
    )
    if existing is not None:
        return existing.id
    paper = await paper_service.create_paper(
        session,
        org_id=org_id,
        user_id=user_id,
        title=rec.title,
        authors=rec.authors,
        doi=rec.doi,
        pmid=rec.pmid,
        journal=rec.journal,
        publication_date=rec.publication_date,
        abstract=rec.abstract,
        provenance=PROVENANCE_LIT_REVIEW_RUN,
        source=rec.source,
    )
    return paper.id


async def _load_run(session: AsyncSession, run_id: int) -> LiteratureReviewRun | None:
    rs = await session.execute(
        select(LiteratureReviewRun).where(LiteratureReviewRun.id == run_id)
    )
    return rs.scalar_one_or_none()


async def _mark_failed(run_id: int, error: str) -> None:
    factory = _database.async_session_factory
    if factory is None:
        return
    async with factory() as s:  # type: ignore[misc]
        run = await _load_run(s, run_id)
        if run is None:
            return
        run.status = SEARCH_FAILED
        run.error_message = error[:500]
        run.completed_at = datetime.now(UTC)
        await s.commit()
    await event_bus.emit(LITERATURE_REVIEW_RUN_FAILED, {"run_id": run_id})


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


async def list_runs_for_experiment(
    session: AsyncSession, *, org_id: int, experiment_id: int
) -> list[LiteratureReviewRun]:
    rs = await session.execute(
        select(LiteratureReviewRun)
        .where(
            LiteratureReviewRun.organization_id == org_id,
            LiteratureReviewRun.experiment_id == experiment_id,
        )
        .order_by(LiteratureReviewRun.created_at.desc())
    )
    return list(rs.scalars().all())


async def get_run(
    session: AsyncSession, *, org_id: int, run_id: int
) -> LiteratureReviewRun | None:
    rs = await session.execute(
        select(LiteratureReviewRun).where(
            LiteratureReviewRun.id == run_id,
            LiteratureReviewRun.organization_id == org_id,
        )
    )
    return rs.scalar_one_or_none()
