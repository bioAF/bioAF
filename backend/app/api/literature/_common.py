"""Shared serialization helper for the Literature API sub-routers.

``_serialize_paper`` is the one helper used by more than one sub-domain (papers,
searches, and recommendations all render the standardized Paper shape), so it
lives here rather than in any single sub-router module.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.literature import LiteraturePaper
from app.schemas.literature import AssociationPayload, AuthorPayload, PaperResponse
from app.services.literature import association_service, paper_service


async def _serialize_paper(session: AsyncSession, paper: LiteraturePaper, user_id: int) -> PaperResponse:
    associations_rows = await association_service.list_for_paper(session, paper.id)
    associations = []
    for a in associations_rows:
        scope_name = await paper_service.scope_name_for(session, a.scope_type, a.scope_id)
        parent_pid, parent_pname = await paper_service.parent_project_for(session, a.scope_type, a.scope_id)
        associations.append(
            AssociationPayload(
                id=a.id,
                scope_type=a.scope_type,
                scope_id=a.scope_id,
                scope_name=scope_name,
                parent_project_id=parent_pid,
                parent_project_name=parent_pname,
                added_by_user_id=a.added_by_user_id,
                added_at=a.added_at,
            )
        )

    return PaperResponse(
        id=paper.id,
        title=paper.title,
        authors=[AuthorPayload(**a) for a in (paper.authors_json or [])],
        publication_date=paper.publication_date,
        journal=paper.journal,
        doi=paper.doi,
        pmid=paper.pmid,
        abstract=paper.abstract,
        provenance=paper.provenance,
        source=paper.source,
        added_by_user_id=paper.added_by_user_id,
        has_pdf=bool(paper.gcs_pdf_uri),
        has_full_text=paper.has_full_text,
        extraction_status=paper.extraction_status,
        extraction_error=paper.extraction_error,
        comment_count=await paper_service.comment_count(session, paper.id),
        reading_status=await paper_service.reading_status_for(session, paper.id, user_id),
        dismissed=await paper_service.is_dismissed(session, paper.id),
        in_library=paper.in_library,
        associations=associations,
        created_at=paper.created_at,
        updated_at=paper.updated_at,
    )
