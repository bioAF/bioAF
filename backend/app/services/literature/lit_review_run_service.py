"""Lit Review Run: on-demand LLM-driven paper recommendation pipeline.

The flow per SPEC-literature-lit-review-run.md, one phase function each:
  1. _prepare: build a compact experiment context block after confirming the
     experiment, LLM provider, and at least one enabled source still exist.
  2. _generate_queries: ask the org's active LLM for up to 5 expansion queries.
  3. _collect_candidates: run each query across all enabled sources, counting
     every source/query attempt, and dedupe; then drop library + dismissed.
  4. _score: ask the LLM to score the remaining candidates 0.0-1.0 with
     one-line reasoning. Unusable output fails the run; a valid empty
     ranking does not.
  5. _persist_recommendations: auto-accept the top N (above score_threshold)
     into the Library, creating Paper rows with provenance=lit_review_run,
     and commit the terminal outcome in the same transaction.
  6. Emit one terminal event: LITERATURE_REVIEW_RUN_COMPLETED for complete
     and partial runs, LITERATURE_REVIEW_RUN_FAILED for failed runs.

Source outcomes decide the status: every attempt failing fails the run, some
failing makes it partial (with error_message naming the sources), none
failing makes it complete even when nothing was found.

LLM calls go through the existing provider client abstraction
(`app.services.llm_provider_clients.get_client`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.database as _database
from app.models.experiment import Experiment
from app.models.literature import (
    LiteratureAssociation,
    LiteraturePaper,
    LiteraturePaperDismissal,
    LiteratureRecommendation,
    LiteratureReviewRun,
    PROVENANCE_LIT_REVIEW_RUN,
    REC_ACCEPTED,
    SCOPE_EXPERIMENT,
    SEARCH_COMPLETE,
    SEARCH_FAILED,
    SEARCH_PARTIAL,
    SEARCH_RUNNING,
    TRIGGER_MANUAL,
    TRIGGER_SCHEDULED,
    derive_bucket,
)
from app.services import audit_service, llm_provider_config_service
from app.services.event_bus import event_bus
from app.services.event_types import (
    LITERATURE_AUTO_REVIEW_RECOMMENDATIONS,
    LITERATURE_REVIEW_RUN_COMPLETED,
    LITERATURE_REVIEW_RUN_FAILED,
)
from app.services.literature import association_service, paper_service, sources_config_service
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


class ScoringUnusable(Exception):
    """The scoring response cannot be read as a ranking. A valid empty array is
    not unusable: it means every candidate was omitted as irrelevant."""


NO_SOURCES_ENABLED = "no_literature_sources_enabled"


class NoLiteratureSourcesEnabled(Exception):
    pass


@dataclass
class _SourceAttempts:
    attempts: int = 0
    failed: int = 0
    failure_categories: set[str] = field(default_factory=set)


@dataclass
class SourceOutcome:
    """Per-source attempt counts across every expansion query.

    Every source/query attempt counts, including successful attempts that
    return zero papers. Failure categories are exception class names only, so
    diagnostics never carry credentials or raw provider payloads."""

    sources: dict[str, _SourceAttempts] = field(default_factory=dict)

    def record(self, source: str, error: BaseException | None = None) -> None:
        entry = self.sources.setdefault(source, _SourceAttempts())
        entry.attempts += 1
        if error is not None:
            entry.failed += 1
            entry.failure_categories.add(type(error).__name__)

    @property
    def status(self) -> str:
        attempts = sum(e.attempts for e in self.sources.values())
        failed = sum(e.failed for e in self.sources.values())
        if failed == 0:
            return SEARCH_COMPLETE
        if failed == attempts:
            return SEARCH_FAILED
        return SEARCH_PARTIAL

    def summary(self) -> str | None:
        """Stable error_message naming each affected source, or None when no attempt failed."""
        status = self.status
        if status == SEARCH_COMPLETE:
            return None
        prefix = "all_literature_sources_failed" if status == SEARCH_FAILED else "some_literature_sources_failed"
        parts = [
            f"{name} {e.failed}/{e.attempts} attempts failed" for name, e in sorted(self.sources.items()) if e.failed
        ]
        return f"{prefix}: {'; '.join(parts)}"

    def audit_details(self) -> dict[str, dict]:
        return {
            name: {
                "attempts": e.attempts,
                "failed": e.failed,
                "failure_categories": sorted(e.failure_categories),
            }
            for name, e in sorted(self.sources.items())
        }


async def create_run(
    session: AsyncSession,
    *,
    org_id: int,
    experiment_id: int,
    triggered_by_user_id: int,
    max_recommendations: int = 10,
    score_threshold: float | None = None,
    api_key_id: int | None = None,
    trigger: str = TRIGGER_MANUAL,
) -> LiteratureReviewRun:
    """Insert a new LiteratureReviewRun row and audit log entry. The caller
    commits and then calls schedule_run().

    If score_threshold is None, fall back to the org's
    lit_review_relevance_threshold (default 0.65). This is the
    admin-controlled lower bound set via Settings > Integrations > LLMs.

    trigger is 'manual' for on-demand runs and 'scheduled' for runs started by
    the automated cadence loop.
    """
    cfg = await llm_provider_config_service.get_active(session, org_id)
    if cfg is None:
        raise NoActiveLlmProvider("org has no active LLM provider")

    rs = await session.execute(select(Experiment).where(Experiment.id == experiment_id))
    experiment = rs.scalar_one_or_none()
    if experiment is None or experiment.organization_id != org_id:
        raise ReviewRunFailed("experiment not found in this org")

    if not await sources_config_service.list_enabled(session, org_id):
        raise NoLiteratureSourcesEnabled(NO_SOURCES_ENABLED)

    if score_threshold is None:
        from app.models.organization import Organization

        org_rs = await session.execute(
            select(Organization.lit_review_relevance_threshold).where(Organization.id == org_id)
        )
        threshold = org_rs.scalar_one_or_none()
        score_threshold = float(threshold) if threshold is not None else 0.65

    run = LiteratureReviewRun(
        organization_id=org_id,
        experiment_id=experiment_id,
        triggered_by_user_id=triggered_by_user_id,
        trigger=trigger,
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


_TERMINAL_STATUSES = (SEARCH_COMPLETE, SEARCH_PARTIAL, SEARCH_FAILED)
_CANCELLED = "cancelled"


class _RunFailure(Exception):
    """A phase ended the run. The message is the persisted diagnostic."""


@dataclass
class _Execution:
    """What one execution has learned so far, so any terminal path (including
    cancellation and unexpected errors) can record it."""

    run_id: int
    sources: SourceOutcome | None = None
    candidate_count: int | None = None


async def _execute_run(run_id: int) -> None:
    """Run one review to a terminal state. Called by schedule_run (manual runs)
    and awaited directly by the scheduled sweep.

    Every terminal path persists status and completed_at before emitting
    exactly one terminal event. Cancellation persists a failure and is
    re-raised; nothing overwrites an outcome that was already committed."""
    factory = _database.async_session_factory
    if factory is None:
        return
    execution = _Execution(run_id)
    try:
        async with factory() as s:  # type: ignore[misc]
            run = await _load_run(s, run_id)
            if run is None:
                return
            run.status = SEARCH_RUNNING
            run.started_at = datetime.now(UTC)
            await s.commit()

        await _do_run(execution)
    except _RunFailure as e:
        await _fail(execution, str(e))
    except asyncio.CancelledError:
        await _fail(execution, _CANCELLED)
        raise
    except Exception as e:
        logger.exception("Lit Review Run %s failed", run_id)
        await _fail(execution, f"unexpected_error: {type(e).__name__}: {e}")


@dataclass(frozen=True)
class _RunContext:
    """Everything the phases need, read once while the run is loaded."""

    run_id: int
    org_id: int
    experiment_id: int
    experiment_name: str
    user_id: int
    trigger: str
    provider: str
    model: str
    api_key: str | None
    context_block: str
    source_api_keys: dict[str, str | None]
    score_threshold: float
    max_recommendations: int


async def _do_run(execution: _Execution) -> None:
    """The review phases in order. A phase that ends the run raises
    _RunFailure; every successful path finishes through _persist_terminal."""
    ctx = await _prepare(execution.run_id)
    if ctx is None:
        return
    queries = await _generate_queries(ctx)
    execution.sources = SourceOutcome()
    candidates = await _collect_candidates(ctx, queries, execution.sources)
    candidates = (await _exclude_library_and_dismissed(ctx.org_id, candidates))[:_DEFAULT_CANDIDATE_CAP]
    execution.candidate_count = len(candidates)

    # Source classification decides the outcome from here on, including when
    # nothing is left to score.
    status = execution.sources.status
    if status == SEARCH_FAILED:
        raise _RunFailure(execution.sources.summary())
    scored = await _score(ctx, candidates) if candidates else []
    rec_count = await _persist_recommendations(ctx, execution, scored, status=status)
    await _emit_terminal(ctx.run_id, status, rec_count)
    await _notify_scheduled_picks(ctx, rec_count)


async def _prepare(run_id: int) -> _RunContext | None:
    """Load the run and check, against current configuration, that its
    experiment, LLM provider, and at least one enabled source still exist.
    Runs before any LLM spend."""
    factory = _database.async_session_factory
    assert factory is not None
    async with factory() as s:  # type: ignore[misc]
        run = await _load_run(s, run_id)
        if run is None:
            return None
        experiment = (
            await s.execute(
                select(Experiment).where(
                    Experiment.id == run.experiment_id,
                    Experiment.organization_id == run.organization_id,
                )
            )
        ).scalar_one_or_none()
        if experiment is None:
            raise _RunFailure("experiment not found")
        provider_cfg = await llm_provider_config_service.get_for_provider(s, run.organization_id, run.llm_provider)
        if provider_cfg is None:
            raise _RunFailure("llm provider configuration disappeared")
        sources = await sources_config_service.list_enabled(s, run.organization_id)
        if not sources:
            raise _RunFailure(NO_SOURCES_ENABLED)
        return _RunContext(
            run_id=run.id,
            org_id=run.organization_id,
            experiment_id=run.experiment_id,
            experiment_name=experiment.name or f"experiment #{run.experiment_id}",
            user_id=run.triggered_by_user_id,
            trigger=run.trigger,
            provider=run.llm_provider,
            model=run.llm_model,
            api_key=provider_cfg.api_key,
            context_block=await _build_experiment_context(s, run, experiment),
            source_api_keys={row.source: row.api_key for row in sources},
            score_threshold=run.score_threshold,
            max_recommendations=run.max_recommendations,
        )


async def _generate_queries(ctx: _RunContext) -> list[str]:
    try:
        queries = await _llm_generate_queries(
            provider=ctx.provider, model=ctx.model, api_key=ctx.api_key, context_block=ctx.context_block
        )
    except ProviderError as e:
        raise _RunFailure(f"llm_query_generation: {e}") from e
    if not queries:
        raise _RunFailure("llm_generated_no_valid_queries")
    factory = _database.async_session_factory
    assert factory is not None
    async with factory() as s:  # type: ignore[misc]
        run = await _load_run(s, ctx.run_id)
        run.expansion_queries_json = queries
        await s.commit()
    return queries


async def _collect_candidates(ctx: _RunContext, queries: list[str], outcome: SourceOutcome) -> list[PaperRecord]:
    """Run every query against every enabled source, recording each attempt in
    `outcome`, and return candidates deduplicated by DOI (else title)."""
    candidates: list[PaperRecord] = []
    seen_keys: set[str] = set()
    for q in queries:
        tasks = {
            src: asyncio.create_task(_safe_source_search(src, q, api_key))
            for src, api_key in ctx.source_api_keys.items()
        }
        try:
            for src, t in tasks.items():
                try:
                    records = await t
                except Exception as e:
                    logger.warning("source %s failed during query '%s': %s", src, q, type(e).__name__)
                    outcome.record(src, e)
                    continue
                outcome.record(src)
                for rec in records:
                    key = (rec.doi or "").lower() or rec.title.lower()
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    candidates.append(rec)
        finally:
            # On cancellation, stop and await every outstanding source call.
            pending = [t for t in tasks.values() if not t.done()]
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
    return candidates


async def _score(ctx: _RunContext, candidates: list[PaperRecord]) -> list[tuple[PaperRecord, float, str]]:
    try:
        return await _llm_score_candidates(
            provider=ctx.provider,
            model=ctx.model,
            api_key=ctx.api_key,
            context_block=ctx.context_block,
            candidates=candidates,
        )
    except (ProviderError, ScoringUnusable) as e:
        raise _RunFailure(f"llm_ranking: {e}") from e


async def _persist_recommendations(
    ctx: _RunContext,
    execution: _Execution,
    scored: list[tuple[PaperRecord, float, str]],
    *,
    status: str,
) -> int:
    """Auto-accept the top scored candidates and commit them together with the
    terminal outcome. Each kept paper joins the Library, is associated with the
    run's experiment, and gets an accepted recommendation whose reasoning shows
    on the paper detail page. Returns the number of recommendations added."""
    kept = [tup for tup in sorted(scored, key=lambda x: x[1], reverse=True) if tup[1] >= ctx.score_threshold]
    factory = _database.async_session_factory
    assert factory is not None
    async with factory() as s:  # type: ignore[misc]
        rec_count = 0
        decided_at = datetime.now(UTC)
        for candidate, score, reasoning in kept[: ctx.max_recommendations]:
            paper_id = await _upsert_lit_review_paper(s, org_id=ctx.org_id, user_id=ctx.user_id, rec=candidate)
            existing = (
                await s.execute(
                    select(LiteratureRecommendation).where(
                        LiteratureRecommendation.organization_id == ctx.org_id,
                        LiteratureRecommendation.paper_id == paper_id,
                        LiteratureRecommendation.experiment_id == ctx.experiment_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                # An earlier recommendation and its user decision stand.
                continue
            s.add(
                LiteratureRecommendation(
                    organization_id=ctx.org_id,
                    paper_id=paper_id,
                    experiment_id=ctx.experiment_id,
                    review_run_id=ctx.run_id,
                    relevance_score=score,
                    relevance_bucket=derive_bucket(score),
                    reasoning=reasoning,
                    status=REC_ACCEPTED,
                    decided_by_user_id=ctx.user_id,
                    decided_at=decided_at,
                )
            )
            paper = await paper_service.get_paper(s, ctx.org_id, paper_id)
            if not paper.in_library:
                paper.in_library = True
            await association_service.get_or_create(
                s,
                paper=paper,
                user_id=ctx.user_id,
                scope_type=SCOPE_EXPERIMENT,
                scope_id=ctx.experiment_id,
            )
            rec_count += 1

        run = await _load_run(s, ctx.run_id)
        await _persist_terminal(s, run, execution, status=status, recommendation_count=rec_count)
    return rec_count


async def _notify_scheduled_picks(ctx: _RunContext, rec_count: int) -> None:
    """Automated cadence runs surface their picks as a per-user in-app
    notification (the notification router fans this out to all active users
    via the seeded in_app rule). Manual runs are surfaced in the UI the user is
    already looking at, so they emit nothing extra. A zero-pick run is silent
    either way."""
    if ctx.trigger != TRIGGER_SCHEDULED or rec_count <= 0:
        return
    plural = "s" if rec_count != 1 else ""
    await event_bus.emit(
        LITERATURE_AUTO_REVIEW_RECOMMENDATIONS,
        {
            "event_type": LITERATURE_AUTO_REVIEW_RECOMMENDATIONS,
            "org_id": ctx.org_id,
            "title": f"AI Lit Review added {rec_count} paper{plural} to {ctx.experiment_name}",
            "message": (
                f"An automated AI Literature Review added {rec_count} new paper{plural} "
                f"to the Library for {ctx.experiment_name}."
            ),
            "entity_type": "literature_review_run",
            "entity_id": ctx.run_id,
            "metadata": {
                "experiment_id": ctx.experiment_id,
                "run_id": ctx.run_id,
                "recommendation_count": rec_count,
            },
        },
    )


async def _persist_terminal(
    s: AsyncSession,
    run: LiteratureReviewRun,
    execution: _Execution,
    *,
    status: str,
    recommendation_count: int | None,
    error: str | None = None,
) -> None:
    """Write the terminal status, counts, diagnostics, and audit entry, then
    commit. A failure's own diagnostic takes precedence over the source summary;
    source diagnostics stay in the audit details either way."""
    sources = execution.sources
    run.status = status
    message = error or (sources.summary() if sources else None)
    run.error_message = message[:500] if message else None
    run.completed_at = datetime.now(UTC)
    if execution.candidate_count is not None:
        run.candidate_count = execution.candidate_count
    if recommendation_count is not None:
        run.recommendation_count = recommendation_count
    details: dict[str, Any] = {
        "status": status,
        "candidate_count": run.candidate_count,
        "recommendation_count": run.recommendation_count,
    }
    if run.error_message:
        details["error"] = run.error_message
    if sources is not None:
        details["sources"] = sources.audit_details()
    await audit_service.log_action(
        s,
        user_id=run.triggered_by_user_id,
        entity_type="literature_review_run",
        entity_id=run.id,
        action="fail" if status == SEARCH_FAILED else "complete",
        details=details,
    )
    await s.commit()


async def _emit_terminal(run_id: int, status: str, recommendation_count: int = 0) -> None:
    if status == SEARCH_FAILED:
        await event_bus.emit(LITERATURE_REVIEW_RUN_FAILED, {"run_id": run_id})
    else:
        await event_bus.emit(
            LITERATURE_REVIEW_RUN_COMPLETED,
            {"run_id": run_id, "recommendation_count": recommendation_count},
        )


async def _fail(execution: _Execution, error: str) -> None:
    """Persist a failure in a fresh session unless a terminal outcome is
    already committed (for example, a notification error after completion)."""
    factory = _database.async_session_factory
    if factory is None:
        return
    async with factory() as s:  # type: ignore[misc]
        run = await _load_run(s, execution.run_id)
        if run is None or run.status in _TERMINAL_STATUSES:
            return
        recommendation_count = 0 if execution.candidate_count is not None else None
        await _persist_terminal(
            s, run, execution, status=SEARCH_FAILED, recommendation_count=recommendation_count, error=error
        )
    await _emit_terminal(execution.run_id, SEARCH_FAILED)


async def _safe_source_search(src: str, query: str, api_key: str | None) -> list[PaperRecord]:
    adapter = get_adapter(src)
    return await asyncio.wait_for(
        adapter.search(query=query, max_results=20, api_key=api_key),
        timeout=_PER_SOURCE_TIMEOUT_SECONDS,
    )


async def _build_experiment_context(session: AsyncSession, run: LiteratureReviewRun, experiment: Experiment) -> str:
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


async def _llm_generate_queries(provider: str, model: str, api_key: str | None, context_block: str) -> list[str]:
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
            first_author = f"{f.get('family', '')}".strip()
        title = c.title.replace("\n", " ")[:300]
        abstract = (c.abstract or "")[:600]
        summaries.append(f"[{i}] {title} | first_author={first_author} | doi={c.doi or 'n/a'}\nAbstract: {abstract}")
    candidate_block = "\n\n".join(summaries)

    system = (
        "You are a research librarian. Score each candidate paper 0.0 to 1.0 on "
        "relevance to the experiment context. Respond with valid JSON only: a "
        'list of objects {"index": int, "score": float, "reasoning": str}. '
        "Use one short sentence for reasoning. Do not include any text outside "
        "the JSON. Omit clearly irrelevant candidates."
    )
    payload = (
        f"Experiment context:\n\n{context_block}\n\n"
        f"Candidate papers:\n\n{candidate_block}\n\n"
        'Return a JSON array. Example: [{"index": 0, "score": 0.82, "reasoning": "..."}]'
    )
    client = get_client(provider)
    output = await client.submit(prompt=system, payload=payload, model=model, api_key=api_key)
    parsed = _parse_scoring_response(output)
    results: list[tuple[PaperRecord, float, str]] = []
    seen_indices: set[int] = set()
    for entry in parsed:
        usable = _usable_score_entry(entry, len(candidates))
        if usable is None:
            continue
        idx, score = usable
        if idx in seen_indices:
            continue
        seen_indices.add(idx)
        reasoning = str(entry.get("reasoning") or "")[:500]
        results.append((candidates[idx], max(0.0, min(1.0, score)), reasoning))
    if parsed and not results:
        raise ScoringUnusable("response has no usable entries")
    return results


def _usable_score_entry(entry: Any, candidate_count: int) -> tuple[int, float] | None:
    """(index, score) for an entry naming a real candidate with a finite score,
    else None. Booleans are rejected even though Python treats them as ints."""
    if not isinstance(entry, dict):
        return None
    raw_index, raw_score = entry.get("index"), entry.get("score")
    if isinstance(raw_index, bool) or isinstance(raw_score, bool):
        return None
    try:
        idx = int(raw_index)  # type: ignore[arg-type]
        score = float(raw_score)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return None
    if not math.isfinite(score) or idx < 0 or idx >= candidate_count:
        return None
    return idx, score


def _parse_scoring_response(text: str | None) -> list[Any]:
    """Return the scoring array from a response that is bare JSON, fenced JSON,
    or an array embedded in prose. Raises ScoringUnusable when no JSON can be
    read or the JSON found is not an array."""
    text = (text or "").strip()
    attempts = [text]
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, flags=re.DOTALL)
    if fence:
        attempts.append(fence.group(1))
    for pattern in (r"\[.*\]", r"\[\s*\{.*\}\s*\]"):
        embedded = re.search(pattern, text, flags=re.DOTALL)
        if embedded:
            attempts.append(embedded.group(0))
    for candidate in attempts:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, list):
            raise ScoringUnusable("response is not a JSON array")
        return parsed
    raise ScoringUnusable("response is not valid JSON")


async def _exclude_library_and_dismissed(org_id: int, candidates: list[PaperRecord]) -> list[PaperRecord]:
    """Drop candidates already in the org's Literature Library or dismissed.

    Papers that exist in literature_papers but only as cached search results
    (in_library=false) remain eligible: the LLM can still surface them and
    we want the user to see the AI's pick on the paper detail page even if
    a prior search already cached the metadata.
    """
    if not candidates:
        return []
    factory = _database.async_session_factory
    assert factory is not None
    async with factory() as s:  # type: ignore[misc]
        dois = [c.doi for c in candidates if c.doi]
        if not dois:
            return candidates
        rs = await s.execute(
            select(LiteraturePaper.doi).where(
                LiteraturePaper.organization_id == org_id,
                LiteraturePaper.in_library.is_(True),
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
                LiteraturePaperDismissal.organization_id == org_id,
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
        in_library=True,
    )
    return paper.id


async def _load_run(session: AsyncSession, run_id: int) -> LiteratureReviewRun | None:
    rs = await session.execute(select(LiteratureReviewRun).where(LiteratureReviewRun.id == run_id))
    return rs.scalar_one_or_none()


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


async def get_run(session: AsyncSession, *, org_id: int, run_id: int) -> LiteratureReviewRun | None:
    rs = await session.execute(
        select(LiteratureReviewRun).where(
            LiteratureReviewRun.id == run_id,
            LiteratureReviewRun.organization_id == org_id,
        )
    )
    return rs.scalar_one_or_none()
