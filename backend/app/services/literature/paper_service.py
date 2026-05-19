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
    ALL_READING_STATUSES,
    EXTRACTION_NONE,
    LiteratureAssociation,
    LiteraturePaper,
    LiteraturePaperComment,
    LiteraturePaperDismissal,
    LiteraturePaperReadingStatus,
    PROVENANCE_LIT_REVIEW_RUN,
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
    in_library: bool | None = None,
    api_key_id: int | None = None,
) -> LiteraturePaper:
    """Insert a new paper. Raises DuplicatePaper if a row already exists
    with the same DOI or title+author key in this org. Caller is responsible
    for committing.

    in_library defaults by provenance: user_upload and lit_review_run land in
    the Library immediately; source_search lands outside the Library until a
    user explicitly adds it.
    """
    if provenance not in ALL_PROVENANCES:
        raise ValueError(f"invalid provenance: {provenance}")

    existing = await find_duplicate(session, org_id=org_id, doi=doi, title=title, authors=authors)
    if existing is not None:
        raise DuplicatePaper(existing.id)

    title_norm = normalize_title(title)
    first_key, last_key = first_and_last_author_keys(authors)

    if in_library is None:
        in_library = provenance in (PROVENANCE_USER_UPLOAD, PROVENANCE_LIT_REVIEW_RUN)

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
        in_library=in_library,
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
    project_id: int | None = None,
    experiment_id: int | None = None,
    provenance: str | None = None,
    added_by_user_id: int | None = None,
    has_full_text: bool | None = None,
    source: str | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    in_library: bool | None = True,
    include_active: bool = True,
    include_dismissed: bool = False,
    reading_statuses: tuple[str, ...] | None = None,
    page: int = 1,
    page_size: int = 50,
    sort: str = "added",
) -> tuple[list[LiteraturePaper], int]:
    """Return (papers, total_count) matching the filter set, paginated.

    in_library: filter by the Library membership flag. Default True (only
    show papers that have been added to the Library). Pass None to include
    everything (used by the search-result detail view).

    include_active / include_dismissed: which dismissal buckets to include.
    Both default to active-only (include_active=True, include_dismissed=False).
    Passing include_dismissed=True returns dismissed papers as well; passing
    include_active=False returns only dismissed papers.

    reading_statuses: optional whitelist of reading statuses to include. The
    values "unread", "reading", "read" filter by the current user's status
    (user_id must be provided). When unset, no reading-status filter is
    applied. Passing an empty tuple yields zero rows.
    """
    if page < 1:
        page = 1
    if page_size < 1 or page_size > 200:
        page_size = 50

    query = select(LiteraturePaper).where(LiteraturePaper.organization_id == org_id)

    if in_library is True:
        query = query.where(LiteraturePaper.in_library.is_(True))
    elif in_library is False:
        query = query.where(LiteraturePaper.in_library.is_(False))

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

    if project_id is not None:
        proj_filter = and_(
            LiteratureAssociation.paper_id == LiteraturePaper.id,
            LiteratureAssociation.scope_type == "project",
            LiteratureAssociation.scope_id == project_id,
            LiteratureAssociation.removed_at.is_(None),
        )
        query = query.where(exists().where(proj_filter))

    if experiment_id is not None:
        exp_filter = and_(
            LiteratureAssociation.paper_id == LiteraturePaper.id,
            LiteratureAssociation.scope_type == "experiment",
            LiteratureAssociation.scope_id == experiment_id,
            LiteratureAssociation.removed_at.is_(None),
        )
        query = query.where(exists().where(exp_filter))

    dismissed_filter = and_(
        LiteraturePaperDismissal.paper_id == LiteraturePaper.id,
        LiteraturePaperDismissal.reversed_at.is_(None),
    )
    if include_active and not include_dismissed:
        query = query.where(~exists().where(dismissed_filter))
    elif include_dismissed and not include_active:
        query = query.where(exists().where(dismissed_filter))
    elif not include_active and not include_dismissed:
        # Nothing matches; short-circuit to an impossible predicate so the
        # caller can keep the rest of the filter logic unchanged.
        query = query.where(LiteraturePaper.id == -1)

    if reading_statuses is not None and user_id is not None:
        if not reading_statuses:
            query = query.where(LiteraturePaper.id == -1)
        else:
            allowed = tuple(s for s in reading_statuses if s in ALL_READING_STATUSES)
            include_unread = "unread" in allowed
            other = tuple(s for s in allowed if s != "unread")
            if not allowed:
                query = query.where(LiteraturePaper.id == -1)
            else:
                rs_filter = and_(
                    LiteraturePaperReadingStatus.paper_id == LiteraturePaper.id,
                    LiteraturePaperReadingStatus.user_id == user_id,
                )
                if include_unread and other:
                    rs_match = and_(rs_filter, LiteraturePaperReadingStatus.status.in_(other))
                    no_row = ~exists().where(rs_filter)
                    query = query.where(exists().where(rs_match) | no_row)
                elif include_unread:
                    query = query.where(~exists().where(rs_filter))
                else:
                    rs_match = and_(rs_filter, LiteraturePaperReadingStatus.status.in_(other))
                    query = query.where(exists().where(rs_match))

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


async def add_to_library(
    session: AsyncSession,
    *,
    paper: LiteraturePaper,
    user_id: int,
    api_key_id: int | None = None,
) -> LiteraturePaper:
    """Flip in_library to true for a search-discovered paper. No-op if the
    paper is already in the library; idempotent and audit-logged either way."""
    if paper.in_library:
        return paper
    paper.in_library = True
    await session.flush()
    await audit_service.log_action(
        session,
        user_id=user_id,
        api_key_id=api_key_id,
        entity_type="literature_paper",
        entity_id=paper.id,
        action="add_to_library",
        details={"provenance": paper.provenance},
    )
    return paper


async def list_recommendation_notes(
    session: AsyncSession, paper_id: int
) -> list[dict[str, Any]]:
    """Return the LLM-authored Lit Review Run reasonings tied to a paper.

    Each entry includes the run id, experiment id, relevance score and
    bucket, and the reasoning string. Rendered on the paper detail page as
    an "AI Lit Review Notes" panel; not a row in literature_paper_comments.
    """
    from app.models.literature import LiteratureRecommendation, LiteratureReviewRun

    rs = await session.execute(
        select(LiteratureRecommendation, LiteratureReviewRun)
        .join(LiteratureReviewRun, LiteratureRecommendation.review_run_id == LiteratureReviewRun.id)
        .where(LiteratureRecommendation.paper_id == paper_id)
        .order_by(LiteratureRecommendation.created_at.desc())
    )
    notes: list[dict[str, Any]] = []
    for rec, run in rs.all():
        notes.append(
            {
                "review_run_id": rec.review_run_id,
                "experiment_id": rec.experiment_id,
                "relevance_score": rec.relevance_score,
                "relevance_bucket": rec.relevance_bucket,
                "reasoning": rec.reasoning,
                "llm_provider": run.llm_provider,
                "llm_model": run.llm_model,
                "created_at": rec.created_at,
            }
        )
    return notes


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
