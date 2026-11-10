"""In-process ad-hoc Literature Search coordinator (ADR-056).

A search runs as one asyncio.gather over per-source coroutines. Each
coroutine calls its adapter, upserts Paper rows into literature_papers,
and writes literature_search_results rows. The parent literature_searches
row tracks per_source_status and the aggregate status.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.database as _database
from app.exceptions import ValidationError
from app.models.literature import (
    EXTERNAL_SOURCES,
    LiteraturePaper,
    LiteratureSearch,
    LiteratureSearchResult,
    PROVENANCE_SOURCE_SEARCH,
    SEARCH_COMPLETE,
    SEARCH_FAILED,
    SEARCH_PARTIAL,
    SEARCH_RUNNING,
)
from app.services import audit_service
from app.services.event_bus import event_bus
from app.services.event_types import (
    LITERATURE_SEARCH_COMPLETED,
    LITERATURE_SEARCH_FAILED,
)
from app.services.literature import paper_service, source_policy, sources_config_service
from app.services.literature.source_policy import SourceDisabled, SourceExecution
from app.services.literature.sources import PaperRecord, get_adapter
from app import runtime

logger = logging.getLogger("bioaf.literature.search_service")


_PER_SOURCE_TIMEOUT_SECONDS = 300
_DEFAULT_MAX_PER_SOURCE = 50


class SearchNotFound(Exception):
    pass


class SourcesUnavailable(Exception):
    """The search names no enabled source (none enabled, or one it asked for is off)."""


async def create_search(
    session: AsyncSession,
    *,
    org_id: int,
    user_id: int,
    query: str,
    sources: list[str] | None = None,
    max_per_source: int = _DEFAULT_MAX_PER_SOURCE,
    api_key_id: int | None = None,
) -> LiteratureSearch:
    invalid = [s for s in sources or [] if s not in EXTERNAL_SOURCES]
    if invalid:
        raise ValidationError(f"unknown sources: {invalid}")
    # An ordinary search runs the enabled sources; asking for a disabled one is refused.
    resolved = await sources_config_service.resolve(session, org_id, list(sources or EXTERNAL_SOURCES))
    if sources:
        disabled = [s for s in sources if not resolved[s].enabled]
        if disabled:
            raise SourcesUnavailable(f"Turned off in Literature Sources settings: {', '.join(disabled)}")
    else:
        sources = [s for s in EXTERNAL_SOURCES if resolved[s].enabled]
        if not sources:
            raise SourcesUnavailable("No Literature Sources are enabled.")
    search = LiteratureSearch(
        organization_id=org_id,
        user_id=user_id,
        query_text=query,
        sources_json=sources,
        per_source_status={s: "queued" for s in sources},
    )
    session.add(search)
    await session.flush()
    await audit_service.log_action(
        session,
        user_id=user_id,
        api_key_id=api_key_id,
        entity_type="literature_search",
        entity_id=search.id,
        action="create",
        details={"sources": sources, "query": query, "max_per_source": max_per_source},
    )
    return search


async def schedule_search_run(
    *,
    search_id: int,
    user_id: int,
    max_per_source: int = _DEFAULT_MAX_PER_SOURCE,
) -> None:
    runtime.spawn(_run_search(search_id, user_id, max_per_source), role="literature-search")


async def _run_search(search_id: int, user_id: int, max_per_source: int) -> None:
    factory = _database.async_session_factory
    if factory is None:
        return
    async with factory() as s:  # type: ignore[misc]
        rs = await s.execute(select(LiteratureSearch).where(LiteratureSearch.id == search_id))
        search = rs.scalar_one_or_none()
        if search is None:
            return
        sources = list(search.sources_json or [])
        # One configuration snapshot for this search, taken before any network wait.
        executions = await sources_config_service.resolve(s, search.organization_id, sources)
        query_text = search.query_text
        search.status = SEARCH_RUNNING
        search.started_at = datetime.now(UTC)
        await s.commit()

    # Run all source coroutines in their own fresh session to avoid contention.
    results: dict[str, list[PaperRecord] | Exception] = {}
    tasks = {
        src: runtime.spawn(
            _run_source_with_timeout(src, query_text, executions[src], max_per_source),
            role="literature-search-source",
        )
        for src in sources
    }
    try:
        for src, task in tasks.items():
            try:
                results[src] = await task
            except Exception as e:  # pragma: no cover
                results[src] = e
    finally:
        # On cancellation, stop the source calls not yet awaited too, and wait for them all.
        for task in tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)

    async with factory() as s:  # type: ignore[misc]
        rs = await s.execute(select(LiteratureSearch).where(LiteratureSearch.id == search_id))
        search = rs.scalar_one_or_none()
        if search is None:
            return
        per_source: dict[str, str] = dict(search.per_source_status or {})
        total = 0
        succeeded = 0
        failed = 0
        for src, outcome in results.items():
            if isinstance(outcome, Exception):
                per_source[src] = f"failed: {type(outcome).__name__}"
                failed += 1
                continue
            per_source[src] = "complete"
            succeeded += 1
            await _ingest_records(
                s,
                search_id=search_id,
                org_id=search.organization_id,
                user_id=user_id,
                source_name=src,
                records=outcome,
            )
            total += len(outcome)

        search.per_source_status = per_source
        search.completed_at = datetime.now(UTC)
        search.result_count = total
        if failed == len(sources):
            search.status = SEARCH_FAILED
            search.error_message = "all sources failed"
        elif failed > 0:
            search.status = SEARCH_PARTIAL
        else:
            search.status = SEARCH_COMPLETE
        await s.commit()

        # Notify.
        event_type = LITERATURE_SEARCH_COMPLETED if failed != len(sources) else LITERATURE_SEARCH_FAILED
        try:
            await event_bus.emit(
                event_type,
                {
                    "search_id": search_id,
                    "organization_id": search.organization_id,
                    "user_id": user_id,
                    "result_count": total,
                    "status": search.status,
                },
            )
        except Exception:  # pragma: no cover
            logger.exception("event_bus emit failed for search %s", search_id)


async def _run_source_with_timeout(
    src: str, query: str, execution: SourceExecution, max_per_source: int
) -> list[PaperRecord]:
    """One source's search under its configuration snapshot. Queueing, both
    requests of a multi-request source, and retries share the budget."""
    if not execution.enabled:
        raise SourceDisabled(src)
    adapter = get_adapter(src)
    try:
        with source_policy.bound(execution, budget_seconds=_PER_SOURCE_TIMEOUT_SECONDS):
            return await asyncio.wait_for(
                adapter.search(query=query, max_results=max_per_source, api_key=execution.api_key),
                timeout=_PER_SOURCE_TIMEOUT_SECONDS,
            )
    except TimeoutError as e:
        if type(e) is not TimeoutError:
            raise  # a quota wait or retry budget that ran out keeps its own category
        raise TimeoutError(f"source {src} exceeded {_PER_SOURCE_TIMEOUT_SECONDS}s") from e


async def _ingest_records(
    session: AsyncSession,
    *,
    search_id: int,
    org_id: int,
    user_id: int,
    source_name: str,
    records: list[PaperRecord],
) -> None:
    for rank, rec in enumerate(records, start=1):
        if not rec.title:
            continue
        existing = await paper_service.find_duplicate(
            session,
            org_id=org_id,
            doi=rec.doi,
            title=rec.title,
            authors=rec.authors,
        )
        if existing is None:
            try:
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
                    provenance=PROVENANCE_SOURCE_SEARCH,
                    source=source_name,
                    in_library=False,
                )
            except paper_service.DuplicatePaper as e:
                paper = await paper_service.get_paper(session, org_id, e.existing_paper_id)
        else:
            paper = existing

        session.add(
            LiteratureSearchResult(
                search_id=search_id,
                paper_id=paper.id,
                rank=rank,
                source=source_name,
                source_score=rec.source_score,
            )
        )
    await session.flush()


async def get_search(session: AsyncSession, *, org_id: int, search_id: int) -> LiteratureSearch:
    result = await session.execute(
        select(LiteratureSearch).where(
            LiteratureSearch.id == search_id,
            LiteratureSearch.organization_id == org_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise SearchNotFound(f"search {search_id} not found")
    return row


async def list_searches(
    session: AsyncSession,
    *,
    org_id: int,
    user_id: int | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[LiteratureSearch], int]:
    query = select(LiteratureSearch).where(LiteratureSearch.organization_id == org_id)
    if user_id is not None:
        query = query.where(LiteratureSearch.user_id == user_id)
    if status:
        query = query.where(LiteratureSearch.status == status)
    query = query.order_by(LiteratureSearch.created_at.desc())
    from sqlalchemy import func as sa_func

    count_q = select(sa_func.count()).select_from(query.subquery())
    total = int((await session.execute(count_q)).scalar_one())
    offset = (max(page, 1) - 1) * max(page_size, 1)
    query = query.limit(page_size).offset(offset)
    rows = (await session.execute(query)).scalars().all()
    return list(rows), total


async def list_search_results(
    session: AsyncSession, *, search_id: int
) -> list[tuple[LiteratureSearchResult, LiteraturePaper]]:
    rs = await session.execute(
        select(LiteratureSearchResult, LiteraturePaper)
        .join(LiteraturePaper, LiteratureSearchResult.paper_id == LiteraturePaper.id)
        .where(LiteratureSearchResult.search_id == search_id)
        .order_by(LiteratureSearchResult.rank.nullslast())
    )
    return [(r, p) for r, p in rs.all()]
