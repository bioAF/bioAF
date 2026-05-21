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
    exclude_paper_id: int | None = None,
) -> LiteraturePaper | None:
    """Return an existing paper that matches the org-scoped dedup keys, or None.

    DOI is the strong key (case-insensitive, prefix-stripped via lower()).
    Fallback uses normalized title plus first/last author keys, all scoped
    to the organization. Pass exclude_paper_id to skip a known row (used when
    checking whether a PDF upload to paper X collides with a *different*
    paper).
    """
    title_norm = normalize_title(title)
    first_key, last_key = first_and_last_author_keys(authors)

    if doi:
        doi_clean = doi.strip().lower()
        conditions = [
            LiteraturePaper.organization_id == org_id,
            LiteraturePaper.doi == doi_clean,
        ]
        if exclude_paper_id is not None:
            conditions.append(LiteraturePaper.id != exclude_paper_id)
        result = await session.execute(select(LiteraturePaper).where(*conditions))
        existing = result.scalars().first()
        if existing is not None:
            return existing

    if title_norm and first_key:
        conditions = [
            LiteraturePaper.organization_id == org_id,
            LiteraturePaper.title_normalized == title_norm,
            LiteraturePaper.first_author_key == first_key,
            LiteraturePaper.last_author_key == last_key,
        ]
        if exclude_paper_id is not None:
            conditions.append(LiteraturePaper.id != exclude_paper_id)
        result = await session.execute(select(LiteraturePaper).where(*conditions))
        existing = result.scalars().first()
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
    reason: str | None = None,
) -> None:
    """ "Delete" a paper from the Library: purge its stored files from GCS and
    dismiss it org-wide.

    The row itself is kept, along with its abstract, metadata, comments, and
    AI Lit Review notes. Keeping the row preserves dedup history and the
    non-cascading recommendation / search-result references, and leaves the
    paper in the same state as any paper that was found and then dismissed:
    out of the default Library view and excluded from future recommendations.
    Reversing the dismissal restores the paper, but not the deleted PDF.

    GCS deletion is best-effort; the file references are cleared regardless so
    the app stops offering a PDF that may already be gone."""
    from app.services.literature import dismissal_service, upload_service

    paper_id = paper.id

    await upload_service.delete_paper_files(session, paper_id=paper_id)

    paper.gcs_pdf_uri = None
    paper.extracted_text_uri = None
    paper.has_full_text = False
    paper.extraction_status = EXTRACTION_NONE
    paper.extraction_error = None
    await session.flush()

    await dismissal_service.dismiss(
        session,
        paper_id=paper_id,
        org_id=paper.organization_id,
        user_id=user_id,
        reason=reason or "deleted from Library",
        api_key_id=api_key_id,
    )

    await audit_service.log_action(
        session,
        user_id=user_id,
        api_key_id=api_key_id,
        entity_type="literature_paper",
        entity_id=paper_id,
        action="delete",
        details={"purged_files": True},
    )


async def merge_papers(
    session: AsyncSession,
    *,
    survivor: LiteraturePaper,
    duplicate: LiteraturePaper,
    user_id: int,
    api_key_id: int | None = None,
) -> LiteraturePaper:
    """Fold `duplicate` into `survivor`, then delete `duplicate`.

    Comments, AI Lit Review recommendations/notes, associations, reading
    statuses, and search-result rows move to the survivor. Rows that would
    violate a uniqueness constraint on the survivor (a recommendation for the
    same experiment, an association for the same scope, a reading status for
    the same user) are dropped from the duplicate rather than reassigned. An
    active dismissal transfers only if the survivor has none.
    """
    from sqlalchemy import update

    from app.models.literature import (
        LiteratureAssociation,
        LiteraturePaperComment,
        LiteraturePaperDismissal,
        LiteraturePaperReadingStatus,
        LiteratureRecommendation,
        LiteratureSearchResult,
    )

    if survivor.id == duplicate.id:
        return survivor

    # Comments: no per-paper uniqueness, reassign wholesale.
    await session.execute(
        update(LiteraturePaperComment)
        .where(LiteraturePaperComment.paper_id == duplicate.id)
        .values(paper_id=survivor.id)
    )

    # Recommendations: unique on (org, paper, experiment). Drop a duplicate's
    # rec if the survivor already has one for that experiment.
    surv_recs = (
        (
            await session.execute(
                select(LiteratureRecommendation.experiment_id).where(LiteratureRecommendation.paper_id == survivor.id)
            )
        )
        .scalars()
        .all()
    )
    surv_rec_exps = set(surv_recs)
    dup_recs = (
        (
            await session.execute(
                select(LiteratureRecommendation).where(LiteratureRecommendation.paper_id == duplicate.id)
            )
        )
        .scalars()
        .all()
    )
    for rec in dup_recs:
        if rec.experiment_id in surv_rec_exps:
            await session.delete(rec)
        else:
            rec.paper_id = survivor.id

    # Associations: unique active index on (paper, scope_type, scope_id).
    surv_assocs = (
        (
            await session.execute(
                select(LiteratureAssociation).where(
                    LiteratureAssociation.paper_id == survivor.id,
                    LiteratureAssociation.removed_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    surv_assoc_keys = {(a.scope_type, a.scope_id) for a in surv_assocs}
    dup_assocs = (
        (await session.execute(select(LiteratureAssociation).where(LiteratureAssociation.paper_id == duplicate.id)))
        .scalars()
        .all()
    )
    for a in dup_assocs:
        if a.removed_at is None and (a.scope_type, a.scope_id) in surv_assoc_keys:
            await session.delete(a)
        else:
            a.paper_id = survivor.id

    # Reading status: PK (paper, user). Keep the survivor's where they collide.
    surv_rs_users = set(
        (
            await session.execute(
                select(LiteraturePaperReadingStatus.user_id).where(LiteraturePaperReadingStatus.paper_id == survivor.id)
            )
        )
        .scalars()
        .all()
    )
    dup_rs = (
        (
            await session.execute(
                select(LiteraturePaperReadingStatus).where(LiteraturePaperReadingStatus.paper_id == duplicate.id)
            )
        )
        .scalars()
        .all()
    )
    for rs_row in dup_rs:
        if rs_row.user_id in surv_rs_users:
            await session.delete(rs_row)
        else:
            rs_row.paper_id = survivor.id

    # Search results: no uniqueness, reassign wholesale.
    await session.execute(
        update(LiteratureSearchResult)
        .where(LiteratureSearchResult.paper_id == duplicate.id)
        .values(paper_id=survivor.id)
    )

    # Dismissals: PK is paper_id. Transfer an active duplicate dismissal only
    # if the survivor has none; otherwise let it cascade-delete with the row.
    surv_dismissal = (
        await session.execute(select(LiteraturePaperDismissal).where(LiteraturePaperDismissal.paper_id == survivor.id))
    ).scalar_one_or_none()
    if surv_dismissal is None:
        dup_dismissal = (
            await session.execute(
                select(LiteraturePaperDismissal).where(LiteraturePaperDismissal.paper_id == duplicate.id)
            )
        ).scalar_one_or_none()
        if dup_dismissal is not None:
            dup_dismissal.paper_id = survivor.id

    await session.flush()

    dup_id = duplicate.id
    await session.delete(duplicate)
    await session.flush()

    await audit_service.log_action(
        session,
        user_id=user_id,
        api_key_id=api_key_id,
        entity_type="literature_paper",
        entity_id=survivor.id,
        action="merge",
        details={"merged_from_paper_id": dup_id},
    )
    return survivor


async def list_papers(
    session: AsyncSession,
    *,
    org_id: int,
    user_id: int | None = None,
    scope_type: str | None = None,
    scope_id: int | None = None,
    project_id: int | None = None,
    experiment_id: int | None = None,
    include_parent_project: bool = False,
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
        if include_parent_project:
            # Resolve the experiment's parent project once and OR the two
            # association predicates: papers tied to the experiment, plus
            # papers tied to its parent project (if any).
            parent_rs = await session.execute(select(Experiment.project_id).where(Experiment.id == experiment_id))
            parent_pid = parent_rs.scalar_one_or_none()
            if parent_pid is not None:
                parent_filter = and_(
                    LiteratureAssociation.paper_id == LiteraturePaper.id,
                    LiteratureAssociation.scope_type == "project",
                    LiteratureAssociation.scope_id == parent_pid,
                    LiteratureAssociation.removed_at.is_(None),
                )
                from sqlalchemy import or_

                query = query.where(or_(exists().where(exp_filter), exists().where(parent_filter)))
            else:
                query = query.where(exists().where(exp_filter))
        else:
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


async def list_recommendation_notes(session: AsyncSession, paper_id: int) -> list[dict[str, Any]]:
    """Return the LLM-authored Lit Review Run reasonings tied to a paper.

    Each entry includes the run id, experiment id and name, parent project
    name, relevance score and bucket, and the reasoning string. Rendered on
    the paper detail page as an "AI Lit Review Notes" panel; not a row in
    literature_paper_comments.
    """
    from app.models.literature import LiteratureRecommendation, LiteratureReviewRun

    rs = await session.execute(
        select(
            LiteratureRecommendation,
            LiteratureReviewRun,
            Experiment.name,
            Project.name,
        )
        .join(LiteratureReviewRun, LiteratureRecommendation.review_run_id == LiteratureReviewRun.id)
        .join(Experiment, Experiment.id == LiteratureRecommendation.experiment_id)
        .outerjoin(Project, Project.id == Experiment.project_id)
        .where(LiteratureRecommendation.paper_id == paper_id)
        .order_by(LiteratureRecommendation.created_at.desc())
    )
    notes: list[dict[str, Any]] = []
    for rec, run, experiment_name, project_name in rs.all():
        notes.append(
            {
                "review_run_id": rec.review_run_id,
                "experiment_id": rec.experiment_id,
                "experiment_name": experiment_name,
                "project_name": project_name,
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


async def parent_project_for(
    session: AsyncSession, scope_type: str, scope_id: int | None
) -> tuple[int | None, str | None]:
    """For an experiment-scope association, return (project_id, project_name)
    of the parent project if one exists. Returns (None, None) for any other
    scope or when the experiment has no parent."""
    if scope_type != "experiment" or scope_id is None:
        return None, None
    result = await session.execute(
        select(Project.id, Project.name)
        .join(Experiment, Experiment.project_id == Project.id)
        .where(Experiment.id == scope_id)
    )
    row = result.first()
    if row is None:
        return None, None
    return int(row[0]), row[1]
