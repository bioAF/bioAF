"""Paper endpoints: list, create, upload, metadata, library, dismiss.

v1 is org-scoped throughout: every endpoint filters on the caller's
organization, so cross-org access is impossible by construction.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.api.literature._common import _serialize_paper
from app.database import get_session
from app.models.literature import ALL_PROVENANCES, ALL_SCOPES
from app.schemas.literature import (
    BulkAddToLibraryRequest,
    BulkAddToLibraryResponse,
    BulkDismissRequest,
    BulkDismissResponse,
    CreatePaperRequest,
    DismissalRequest,
    DismissalResponse,
    PaperListResponse,
    PaperResponse,
    RecommendationNotePayload,
    UpdatePaperRequest,
)
from app.services.literature import association_service, dismissal_service, paper_service, storage, upload_service
from app.services.literature.paper_service import DuplicatePaper, PaperNotFound

router = APIRouter()

# Shown when a PDF upload is attempted before the org's Literature GCS bucket
# exists. The upload is rejected rather than silently stored nowhere.
_NO_LITERATURE_STORAGE = (
    "Literature storage is not provisioned, so the file cannot be stored. "
    "Ask an admin to deploy it from Infrastructure > Components using "
    '"Check for Infrastructure Updates".'
)


async def _download_pdf_bytes(session: AsyncSession, gcs_uri: str) -> bytes | None:
    from app.adapters.registry import get_storage_adapter

    try:
        return await get_storage_adapter().read_bytes(gcs_uri)
    except Exception:
        return None


@router.get("/papers", response_model=PaperListResponse)
async def list_papers_endpoint(
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
    scope_type: str | None = Query(None),
    scope_id: int | None = Query(None),
    project_id: int | None = Query(None),
    experiment_id: int | None = Query(None),
    include_parent_project: bool = Query(False),
    provenance: str | None = Query(None),
    added_by_user_id: int | None = Query(None),
    has_full_text: bool | None = Query(None),
    source: str | None = Query(None),
    year_min: int | None = Query(None),
    year_max: int | None = Query(None),
    in_library: bool | None = Query(True),
    include_active: bool = Query(True),
    include_dismissed: bool = Query(False),
    reading_status: list[str] | None = Query(None),
    sort: str = Query("added"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    if scope_type and scope_type not in ALL_SCOPES:
        raise HTTPException(400, "invalid scope_type")
    if provenance and provenance not in ALL_PROVENANCES:
        raise HTTPException(400, "invalid provenance")

    user_id = int(current_user["sub"])
    reading_filter: tuple[str, ...] | None = None
    if reading_status is not None:
        reading_filter = tuple(reading_status)

    papers, total = await paper_service.list_papers(
        session,
        org_id=int(current_user["org_id"]),
        user_id=user_id,
        scope_type=scope_type,
        scope_id=scope_id,
        project_id=project_id,
        experiment_id=experiment_id,
        include_parent_project=include_parent_project,
        provenance=provenance,
        added_by_user_id=added_by_user_id,
        has_full_text=has_full_text,
        source=source,
        year_min=year_min,
        year_max=year_max,
        in_library=in_library,
        include_active=include_active,
        include_dismissed=include_dismissed,
        reading_statuses=reading_filter,
        page=page,
        page_size=page_size,
        sort=sort,
    )
    items = [await _serialize_paper(session, p, user_id) for p in papers]
    return PaperListResponse(items=items, total=total, page=page, page_size=page_size)


@router.post("/papers/upload", response_model=PaperResponse)
async def upload_paper_pdf_endpoint(
    response: Response,
    file: UploadFile = File(...),
    title: str | None = Form(None),
    authors_json: str | None = Form(None),
    doi: str | None = Form(None),
    journal: str | None = Form(None),
    abstract: str | None = Form(None),
    current_user: dict = require_permission("literature", "upload"),
    session: AsyncSession = Depends(get_session),
):
    """Upload a PDF, extract metadata synchronously for the pre-fill form, and
    create the Paper row. Full-text extraction runs as an asyncio background
    task and updates the row when it completes."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "only PDF uploads are supported")
    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(400, "uploaded file is empty")

    if not await storage.get_literature_bucket(session):
        raise HTTPException(503, _NO_LITERATURE_STORAGE)

    extracted = upload_service.synchronous_extract_basic(pdf_bytes)

    import json as _json

    parsed_authors: list[dict] | None = None
    if authors_json:
        try:
            parsed_authors = _json.loads(authors_json)
        except _json.JSONDecodeError:
            raise HTTPException(400, "authors_json must be valid JSON")

    resolved_title = title or extracted.get("title") or file.filename
    resolved_authors = parsed_authors if parsed_authors is not None else extracted.get("authors") or []
    resolved_doi = doi or extracted.get("doi")
    resolved_journal = journal or extracted.get("journal")
    resolved_abstract = abstract or extracted.get("abstract")
    resolved_pub_date = extracted.get("publication_date")

    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    try:
        paper = await paper_service.create_paper(
            session,
            org_id=org_id,
            user_id=user_id,
            title=resolved_title,
            authors=resolved_authors,
            doi=resolved_doi,
            journal=resolved_journal,
            publication_date=resolved_pub_date,
            abstract=resolved_abstract,
            provenance="user_upload",
            source="upload",
        )
    except DuplicatePaper as e:
        # The paper is already in this org (commonly an abstract-only entry
        # from a search or AI recommendation). Attach the uploaded PDF to that
        # entry and pull it into the Library, rather than dropping the file and
        # silently sending the user to a paper with no PDF.
        existing = await paper_service.get_paper(session, org_id, e.existing_paper_id)
        uri = await upload_service.upload_pdf_to_gcs(session, paper_id=existing.id, pdf_bytes=pdf_bytes)
        if uri:
            existing.gcs_pdf_uri = uri
        await paper_service.add_to_library(session, paper=existing, user_id=user_id)
        await upload_service.mark_extraction_pending(session, paper=existing, user_id=user_id)
        await session.commit()
        await upload_service.schedule_extraction(paper_id=existing.id, pdf_bytes=pdf_bytes, user_id=user_id)
        response.status_code = 200
        await session.refresh(existing)
        return await _serialize_paper(session, existing, user_id)

    uri = await upload_service.upload_pdf_to_gcs(session, paper_id=paper.id, pdf_bytes=pdf_bytes)
    if uri:
        paper.gcs_pdf_uri = uri
    await upload_service.mark_extraction_pending(session, paper=paper, user_id=user_id)
    await session.commit()

    # Fire and forget the heavy extraction (full text + persistence).
    await upload_service.schedule_extraction(paper_id=paper.id, pdf_bytes=pdf_bytes, user_id=user_id)

    response.status_code = 201
    await session.refresh(paper)
    return await _serialize_paper(session, paper, user_id)


@router.post("/papers/{paper_id}/extract", response_model=PaperResponse)
async def re_extract_paper_endpoint(
    paper_id: int,
    current_user: dict = require_permission("literature", "upload"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    try:
        paper = await paper_service.get_paper(session, org_id, paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")
    if not paper.gcs_pdf_uri:
        raise HTTPException(400, "paper has no PDF to extract")

    pdf_bytes = await _download_pdf_bytes(session, paper.gcs_pdf_uri)
    if pdf_bytes is None:
        raise HTTPException(500, "could not download paper PDF")

    await upload_service.mark_extraction_pending(session, paper=paper, user_id=int(current_user["sub"]))
    await session.commit()
    await upload_service.schedule_extraction(paper_id=paper_id, pdf_bytes=pdf_bytes, user_id=int(current_user["sub"]))
    await session.refresh(paper)
    return await _serialize_paper(session, paper, int(current_user["sub"]))


@router.post("/papers/{paper_id}/upload-pdf", response_model=PaperResponse)
async def upload_pdf_to_paper_endpoint(
    paper_id: int,
    response: Response,
    file: UploadFile = File(...),
    confirm_merge: bool = Query(False),
    current_user: dict = require_permission("literature", "upload"),
    session: AsyncSession = Depends(get_session),
):
    """Attach a full-text PDF to an existing (often abstract-only) paper.

    The PDF's DOI backfills the paper when it has none. If that DOI already
    belongs to a *different* paper in the org, the first call returns 409 with
    that paper's id and title; calling again with confirm_merge=true folds the
    other paper (its comments, AI notes, associations, reading statuses) into
    this one and deletes it before attaching the PDF.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "only PDF uploads are supported")
    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(400, "uploaded file is empty")

    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    try:
        paper = await paper_service.get_paper(session, org_id, paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")

    if not await storage.get_literature_bucket(session):
        raise HTTPException(503, _NO_LITERATURE_STORAGE)

    extracted = upload_service.synchronous_extract_basic(pdf_bytes)
    effective_doi = paper.doi or extracted.get("doi")

    if effective_doi:
        conflict = await paper_service.find_duplicate(
            session,
            org_id=org_id,
            doi=effective_doi,
            title=paper.title,
            authors=paper.authors_json,
            exclude_paper_id=paper.id,
        )
        if conflict is not None:
            if not confirm_merge:
                raise HTTPException(
                    409,
                    detail={
                        "error": "doi_conflict",
                        "other_paper_id": conflict.id,
                        "other_paper_title": conflict.title,
                        "doi": effective_doi,
                    },
                )
            await paper_service.merge_papers(session, survivor=paper, duplicate=conflict, user_id=user_id)

    # Backfill the DOI on the target if it lacked one.
    if not paper.doi and extracted.get("doi"):
        await paper_service.update_paper_metadata(
            session, paper=paper, user_id=user_id, fields={"doi": extracted["doi"]}
        )

    uri = await upload_service.upload_pdf_to_gcs(session, paper_id=paper.id, pdf_bytes=pdf_bytes)
    if uri:
        paper.gcs_pdf_uri = uri
    await upload_service.mark_extraction_pending(session, paper=paper, user_id=user_id)
    await session.commit()

    await upload_service.schedule_extraction(paper_id=paper.id, pdf_bytes=pdf_bytes, user_id=user_id)
    await session.refresh(paper)
    response.status_code = 200
    return await _serialize_paper(session, paper, user_id)


@router.get("/papers/{paper_id}/pdf")
async def download_pdf_endpoint(
    paper_id: int,
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    try:
        paper = await paper_service.get_paper(session, org_id, paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")
    if not paper.gcs_pdf_uri:
        raise HTTPException(404, "no PDF uploaded for this paper")
    data = await _download_pdf_bytes(session, paper.gcs_pdf_uri)
    if data is None:
        raise HTTPException(500, "could not fetch PDF")

    def stream():
        yield data

    return StreamingResponse(stream(), media_type="application/pdf")


@router.post("/papers", response_model=PaperResponse)
async def create_paper_endpoint(
    body: CreatePaperRequest,
    response: Response,
    current_user: dict = require_permission("literature", "upload"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    authors_dicts = [a.model_dump(exclude_none=True) for a in body.authors]
    try:
        paper = await paper_service.create_paper(
            session,
            org_id=org_id,
            user_id=user_id,
            title=body.title,
            authors=authors_dicts,
            doi=body.doi,
            pmid=body.pmid,
            journal=body.journal,
            publication_date=body.publication_date,
            abstract=body.abstract,
        )
    except DuplicatePaper as e:
        existing = await paper_service.get_paper(session, org_id, e.existing_paper_id)
        response.status_code = 200
        return await _serialize_paper(session, existing, user_id)
    response.status_code = 201
    for assoc in body.associations:
        if "scope_type" not in assoc:
            continue
        await association_service.get_or_create(
            session,
            paper=paper,
            user_id=user_id,
            scope_type=assoc["scope_type"],
            scope_id=assoc.get("scope_id"),
        )
    await session.commit()
    await session.refresh(paper)
    return await _serialize_paper(session, paper, user_id)


@router.get("/papers/{paper_id}", response_model=PaperResponse)
async def get_paper_endpoint(
    paper_id: int,
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    try:
        paper = await paper_service.get_paper(session, int(current_user["org_id"]), paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")
    return await _serialize_paper(session, paper, int(current_user["sub"]))


@router.get(
    "/papers/{paper_id}/recommendation-notes",
    response_model=list[RecommendationNotePayload],
)
async def list_recommendation_notes_endpoint(
    paper_id: int,
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    try:
        await paper_service.get_paper(session, int(current_user["org_id"]), paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")
    notes = await paper_service.list_recommendation_notes(session, paper_id)
    return [RecommendationNotePayload(**n) for n in notes]


@router.post("/papers/{paper_id}/add-to-library", response_model=PaperResponse)
async def add_paper_to_library_endpoint(
    paper_id: int,
    current_user: dict = require_permission("literature", "upload"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    try:
        paper = await paper_service.get_paper(session, org_id, paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")
    await paper_service.add_to_library(session, paper=paper, user_id=user_id)
    await session.commit()
    await session.refresh(paper)
    return await _serialize_paper(session, paper, user_id)


@router.post("/papers/bulk-add-to-library", response_model=BulkAddToLibraryResponse)
async def bulk_add_to_library_endpoint(
    body: BulkAddToLibraryRequest,
    current_user: dict = require_permission("literature", "upload"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    added: list[int] = []
    not_found: list[int] = []
    for pid in body.paper_ids:
        try:
            paper = await paper_service.get_paper(session, org_id, pid)
        except PaperNotFound:
            not_found.append(pid)
            continue
        await paper_service.add_to_library(session, paper=paper, user_id=user_id)
        added.append(pid)
    await session.commit()
    return BulkAddToLibraryResponse(added=added, not_found=not_found)


@router.post("/papers/bulk-dismiss", response_model=BulkDismissResponse)
async def bulk_dismiss_endpoint(
    body: BulkDismissRequest,
    current_user: dict = require_permission("literature", "dismiss"),
    session: AsyncSession = Depends(get_session),
):
    """Dismiss several papers at once (org-wide, idempotent). Used by the
    Library's bulk Dismiss action when a user recognizes selected papers are not
    relevant. Dismissal is reversible by an admin, same as a single dismiss."""
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    dismissed: list[int] = []
    not_found: list[int] = []
    for pid in body.paper_ids:
        try:
            await paper_service.get_paper(session, org_id, pid)
        except PaperNotFound:
            not_found.append(pid)
            continue
        await dismissal_service.dismiss(session, paper_id=pid, org_id=org_id, user_id=user_id, reason=body.reason)
        dismissed.append(pid)
    await session.commit()
    return BulkDismissResponse(dismissed=dismissed, not_found=not_found)


@router.patch("/papers/{paper_id}", response_model=PaperResponse)
async def update_paper_endpoint(
    paper_id: int,
    body: UpdatePaperRequest,
    current_user: dict = require_permission("literature", "upload"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    try:
        paper = await paper_service.get_paper(session, org_id, paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")
    fields = body.model_dump(exclude_unset=True)
    if "authors" in fields and fields["authors"] is not None:
        fields["authors"] = [a if isinstance(a, dict) else a.model_dump(exclude_none=True) for a in fields["authors"]]
    await paper_service.update_paper_metadata(session, paper=paper, user_id=user_id, fields=fields)
    await session.commit()
    await session.refresh(paper)
    return await _serialize_paper(session, paper, user_id)


@router.delete("/papers/{paper_id}", status_code=204)
async def delete_paper_endpoint(
    paper_id: int,
    current_user: dict = require_permission("literature", "delete_paper"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    try:
        paper = await paper_service.get_paper(session, org_id, paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")
    await paper_service.delete_paper(session, paper=paper, user_id=int(current_user["sub"]))
    await session.commit()


@router.post("/papers/{paper_id}/dismiss", response_model=DismissalResponse)
async def dismiss_paper_endpoint(
    paper_id: int,
    body: DismissalRequest,
    current_user: dict = require_permission("literature", "dismiss"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    try:
        await paper_service.get_paper(session, org_id, paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")
    dismissal = await dismissal_service.dismiss(
        session,
        paper_id=paper_id,
        org_id=org_id,
        user_id=int(current_user["sub"]),
        reason=body.reason,
    )
    await session.commit()
    return DismissalResponse(
        paper_id=dismissal.paper_id,
        organization_id=dismissal.organization_id,
        dismissed_by_user_id=dismissal.dismissed_by_user_id,
        reason=dismissal.reason,
        dismissed_at=dismissal.dismissed_at,
        reversed_at=dismissal.reversed_at,
        reversed_by_user_id=dismissal.reversed_by_user_id,
    )


@router.post("/papers/{paper_id}/dismiss/reverse", response_model=DismissalResponse)
async def reverse_dismissal_endpoint(
    paper_id: int,
    current_user: dict = require_permission("literature", "reverse_dismiss"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    try:
        await paper_service.get_paper(session, org_id, paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")
    try:
        dismissal = await dismissal_service.reverse(session, paper_id=paper_id, user_id=int(current_user["sub"]))
    except Exception:
        raise HTTPException(404, "no active dismissal")
    await session.commit()
    return DismissalResponse(
        paper_id=dismissal.paper_id,
        organization_id=dismissal.organization_id,
        dismissed_by_user_id=dismissal.dismissed_by_user_id,
        reason=dismissal.reason,
        dismissed_at=dismissal.dismissed_at,
        reversed_at=dismissal.reversed_at,
        reversed_by_user_id=dismissal.reversed_by_user_id,
    )
