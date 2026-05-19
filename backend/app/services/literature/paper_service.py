"""Paper service: create / fetch / list / soft-delete papers.

All mutations are wrapped in audit_service.log_action calls within the
caller's transaction. The service never commits.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy import and_, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.experiment import Experiment
from app.models.literature import (
    ALL_PROVENANCES,
    EXTRACTION_NONE,
    LiteratureAssociation,
    LiteraturePaper,
    LiteraturePaperComment,
    LiteraturePaperDismissal,
    LiteraturePaperReadingStatus,
    PROVENANCE_USER_UPLOAD,
)
from app.models.project import Project
from app.services import audit_service
from app.services.literature.dedup import (
    first_and_last_author_keys,
    normalize_title,
)

logger = logging.getLogger("bioaf.literature.paper_service")


class PaperNotFound(Exception):
    pass


class DuplicatePaper(Exception):
    """Raised when an insert tries to create a paper that matches an existing
    org-scoped DOI or title+author fallback key. Carries the existing paper id."""

    def __init__(self, existing_paper_id: int) -> None:
        super().__init__(f"paper already exists with id={existing_paper_id}")
        self.existing_paper_id = existing_paper_id


async def find_duplicate(
    session: AsyncSession,
    *,
    org_id: int,
    doi: str | None,
    title: str,
    authors: list[dict] | None,
) -> LiteraturePaper | None:
    """Return an existing paper that matches the org-scoped dedup keys, or None.

    DOI is the strong key (case-insensitive, prefix-stripped via lower()).
    Fallback uses normalized title plus first/last author keys, all scoped
    to the organization.
    """
    title_norm = normalize_title(title)
    first_key, last_key = first_and_last_author_keys(authors)

    if doi:
        doi_clean = doi.strip().lower()
        result = await session.execute(
            select(LiteraturePaper).where(
                LiteraturePaper.organization_id == org_id,
                LiteraturePaper.doi == doi_clean,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing

    if title_norm and first_key:
        result = await session.execute(
            select(LiteraturePaper).where(
                LiteraturePaper.organization_id == org_id,
                LiteraturePaper.title_normalized == title_norm,
                LiteraturePaper.first_author_key == first_key,
                LiteraturePaper.last_author_key == last_key,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing

    return None


async def create_paper(
    session: AsyncSession,
    *,
    org_id: int,
    user_id: int,
    title: str,
    authors: list[dict] | None,
    doi: str | None = None,
    pmid: str | None = None,
    arxiv_id: str | None = None,
    biorxiv_id: str | None = None,
    journal: str | None = None,
    publication_date: date | None = None,
    abstract: str | None = None,
    provenance: str = PROVENANCE_USER_UPLOAD,
    source: str | None = "upload",
    api_key_id: int | None = None,
) -> LiteraturePaper:
    """Insert a new paper. Raises DuplicatePaper if a row already exists
    with the same DOI or title+author key in this org. Caller is responsible
    for committing."""
    if provenance not in ALL_PROVENANCES:
        raise ValueError(f"invalid provenance: {provenance}")

    existing = await find_duplicate(session, org_id=org_id, doi=doi, title=title, authors=authors)
    if existing is not None:
        raise DuplicatePaper(existing.id)

    title_norm = normalize_title(title)
    first_key, last_key = first_and_last_author_keys(authors)

    paper = LiteraturePaper(
        organization_id=org_id,
        doi=(doi.strip().lower() if doi else None),
        pmid=pmid,
        arxiv_id=arxiv_id,
        biorxiv_id=biorxiv_id,
        title=title,
        title_normalized=title_norm,
        authors_json=list(authors or []),
        first_author_key=first_key,
        last_author_key=last_key,
        journal=journal,
        publication_date=publication_date,
        abstract=abstract,
        provenance=provenance,
        added_by_user_id=user_id,
        source=source,
        extraction_status=EXTRACTION_NONE,
    )
    session.add(paper)
    await session.flush()

    await audit_service.log_action(
        session,
        user_id=user_id,
        api_key_id=api_key_id,
        entity_type="literature_paper",
        entity_id=paper.id,
        action="create",
        details={
            "provenance": provenance,
            "source": source,
            "doi": paper.doi,
            "title": title,
        },
    )
    return paper


async def get_paper(session: AsyncSession, org_id: int, paper_id: int) -> LiteraturePaper:
    result = await session.execute(
        select(LiteraturePaper).where(
            LiteraturePaper.id == paper_id,
            LiteraturePaper.organization_id == org_id,
        )
    )
    paper = result.scalar_one_or_none()
    if paper is None:
        raise PaperNotFound(f"paper {paper_id} not found in org {org_id}")
    return paper


async def update_paper_metadata(
    session: AsyncSession,
    *,
    paper: LiteraturePaper,
    user_id: int,
    api_key_id: int | None = None,
    fields: dict[str, Any],
) -> LiteraturePaper:
    """Update mutable metadata fields. Re-derives normalized title and author
    keys when title or authors change. Logs an audit entry with previous_value."""
    allowed = {"title", "authors", "doi", "pmid", "journal", "publication_date", "abstract"}
    previous: dict[str, Any] = {}
    changed_keys: list[str] = []

    if "title" in fields:
        title = fields["title"]
        if title != paper.title:
            previous["title"] = paper.title
            paper.title = title
            paper.title_normalized = normalize_title(title)
            changed_keys.append("title")

    if "authors" in fields:
        authors = fields["authors"]
        if authors != paper.authors_json:
            previous["authors"] = paper.authors_json
            paper.authors_json = list(authors or [])
            first_key, last_key = first_and_last_author_keys(authors)
            paper.first_author_key = first_key
            paper.last_author_key = last_key
            changed_keys.append("authors")

    for k in ("doi", "pmid", "journal", "abstract"):
        if k in fields and fields[k] != getattr(paper, k):
            previous[k] = getattr(paper, k)
            value = fields[k]
            if k == "doi" and value:
                value = value.strip().lower()
            setattr(paper, k, value)
            changed_keys.append(k)

    if "publication_date" in fields:
        new_date = fields["publication_date"]
        if new_date != paper.publication_date:
            previous["publication_date"] = paper.publication_date.isoformat() if paper.publication_date else None
            paper.publication_date = new_date
            changed_keys.append("publication_date")

    extra = set(fields) - allowed
    if extra:
        raise ValueError(f"unknown fields: {sorted(extra)}")

    if not changed_keys:
        return paper

    await session.flush()
    await audit_service.log_action(
        session,
        user_id=user_id,
        api_key_id=api_key_id,
        entity_type="literature_paper",
        entity_id=paper.id,
        action="update",
        details={"changed_fields": changed_keys},
        previous_value=previous,
    )
    return paper


async def delete_paper(
    session: AsyncSession,
    *,
    paper: LiteraturePaper,
    user_id: int,
    api_key_id: int | None = None,
) -> None:
    """Hard delete a Paper. Cascade removes comments, associations, reading
    statuses, dismissals, search-result rows, and recommendations are
    blocked by FK so we orphan-protect via the caller checking."""
    paper_id = paper.id
    await session.delete(paper)
    await session.flush()
    await audit_service.log_action(
        session,
        user_id=user_id,
        api_key_id=api_key_id,
        entity_type="literature_paper",
        entity_id=paper_id,
        action="delete",
    )


async def list_papers(
    session: AsyncSession,
    *,
    org_id: int,
    user_id: int | None = None,
    scope_type: str | None = None,
    scope_id: int | None = None,
    provenance: str | None = None,
    added_by_user_id: int | None = None,
    has_full_text: bool | None = None,
    source: str | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    show_dismissed: bool = False,
    page: int = 1,
    page_size: int = 50,
    sort: str = "added",
) -> tuple[list[LiteraturePaper], int]:
    """Return (papers, total_count) matching the filter set, paginated."""
    if page < 1:
        page = 1
    if page_size < 1 or page_size > 200:
        page_size = 50

    query = select(LiteraturePaper).where(LiteraturePaper.organization_id == org_id)

    if provenance:
        query = query.where(LiteraturePaper.provenance == provenance)
    if added_by_user_id is not None:
        query = query.where(LiteraturePaper.added_by_user_id == added_by_user_id)
    if has_full_text is not None:
        query = query.where(LiteraturePaper.has_full_text == has_full_text)
    if source:
        query = query.where(LiteraturePaper.source == source)
    if year_min is not None:
        query = query.where(LiteraturePaper.publication_date >= date(year_min, 1, 1))
    if year_max is not None:
        query = query.where(LiteraturePaper.publication_date <= date(year_max, 12, 31))

    if scope_type:
        assoc_filter = and_(
            LiteratureAssociation.paper_id == LiteraturePaper.id,
            LiteratureAssociation.scope_type == scope_type,
            LiteratureAssociation.removed_at.is_(None),
        )
        if scope_id is not None:
            assoc_filter = and_(assoc_filter, LiteratureAssociation.scope_id == scope_id)
        else:
            assoc_filter = and_(assoc_filter, LiteratureAssociation.scope_id.is_(None))
        query = query.where(exists().where(assoc_filter))

    if not show_dismissed:
        dismissed_filter = and_(
            LiteraturePaperDismissal.paper_id == LiteraturePaper.id,
            LiteraturePaperDismissal.reversed_at.is_(None),
        )
        query = query.where(~exists().where(dismissed_filter))

    if sort == "title":
        query = query.order_by(LiteraturePaper.title)
    elif sort == "year":
        query = query.order_by(LiteraturePaper.publication_date.desc().nullslast(), LiteraturePaper.id.desc())
    elif sort == "comments":
        query = query.order_by(LiteraturePaper.created_at.desc())
    else:
        query = query.order_by(LiteraturePaper.created_at.desc())

    from sqlalchemy import func as sa_func

    count_query = select(sa_func.count()).select_from(query.subquery())
    count_result = await session.execute(count_query)
    total = int(count_result.scalar_one())

    offset = (page - 1) * page_size
    query = query.limit(page_size).offset(offset)
    result = await session.execute(query)
    return list(result.scalars().all()), total


async def is_dismissed(session: AsyncSession, paper_id: int) -> bool:
    result = await session.execute(
        select(LiteraturePaperDismissal).where(
            LiteraturePaperDismissal.paper_id == paper_id,
            LiteraturePaperDismissal.reversed_at.is_(None),
        )
    )
    return result.scalar_one_or_none() is not None


async def comment_count(session: AsyncSession, paper_id: int) -> int:
    result = await session.execute(
        select(LiteraturePaperComment.id).where(
            LiteraturePaperComment.paper_id == paper_id,
            LiteraturePaperComment.deleted_at.is_(None),
        )
    )
    return len(result.fetchall())


async def reading_status_for(session: AsyncSession, paper_id: int, user_id: int) -> str | None:
    result = await session.execute(
        select(LiteraturePaperReadingStatus.status).where(
            LiteraturePaperReadingStatus.paper_id == paper_id,
            LiteraturePaperReadingStatus.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def scope_name_for(session: AsyncSession, scope_type: str, scope_id: int | None) -> str | None:
    if scope_id is None or scope_type == "global":
        return None
    if scope_type == "project":
        result = await session.execute(select(Project.name).where(Project.id == scope_id))
        return result.scalar_one_or_none()
    if scope_type == "experiment":
        result = await session.execute(select(Experiment.name).where(Experiment.id == scope_id))
        return result.scalar_one_or_none()
    return None
