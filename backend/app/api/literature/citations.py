"""Citation export endpoints: single-paper and bulk BibTeX/RIS."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.models.literature import LiteraturePaper
from app.schemas.literature import CitationBulkRequest
from app.services.literature import citation_service, paper_service
from app.services.literature.paper_service import PaperNotFound

router = APIRouter()


@router.get("/papers/{paper_id}/citation", response_class=PlainTextResponse)
async def single_citation_endpoint(
    paper_id: int,
    format: Literal["bibtex", "ris"] = Query("bibtex"),
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    try:
        paper = await paper_service.get_paper(session, org_id, paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")
    if format == "bibtex":
        return citation_service.to_bibtex(paper)
    return citation_service.to_ris(paper)


@router.post("/citations/bulk", response_class=PlainTextResponse)
async def bulk_citation_endpoint(
    body: CitationBulkRequest,
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    papers: list[LiteraturePaper] = []
    if body.paper_ids:
        for pid in body.paper_ids:
            try:
                papers.append(await paper_service.get_paper(session, org_id, pid))
            except PaperNotFound:
                continue
    elif body.scope_type:
        rows, _ = await paper_service.list_papers(
            session,
            org_id=org_id,
            scope_type=body.scope_type,
            scope_id=body.scope_id,
            page=1,
            page_size=200,
        )
        papers.extend(rows)
    return citation_service.bulk_export(papers, body.format)
