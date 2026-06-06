"""Lab Glossary API (ADR-062). Endpoints under /api/lab-knowledge/glossary.

Term CRUD plus the scan/import/review surface. LLM scans are dispatched as
background tasks (mirroring agent_reviews.py); CSV import parses synchronously.
Delete is gated on ``lab_glossary:delete`` (admin-only by default); all other
mutations require ``lab_glossary:manage``.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import async_session_factory, get_session
from app.schemas.experiment import UserSummary
from app.schemas.lab_glossary import (
    LabGlossaryPendingResponse,
    LabGlossaryProposalListResponse,
    LabGlossaryProposalResponse,
    LabGlossaryReviewRequest,
    LabGlossaryReviewResponse,
    LabGlossaryScanJobResponse,
    LabGlossaryScanRequest,
    LabGlossaryTermCreate,
    LabGlossaryTermListResponse,
    LabGlossaryTermResponse,
    LabGlossaryTermUpdate,
)
from app.services import lab_glossary_scan_service as scan_svc
from app.services.lab_glossary_service import DuplicateTermError, LabGlossaryService

router = APIRouter(prefix="/api/lab-knowledge", tags=["lab-knowledge"])


def _user_summary(user) -> UserSummary | None:
    if user is None:
        return None
    return UserSummary(id=user.id, name=user.name, email=user.email)


def _term_response(t) -> LabGlossaryTermResponse:
    return LabGlossaryTermResponse(
        id=t.id,
        term=t.term,
        definition=t.definition,
        aliases=t.aliases,
        category=t.category,
        context=t.context,
        source=t.source,
        created_by=_user_summary(getattr(t, "created_by", None)),
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


def _job_response(j) -> LabGlossaryScanJobResponse:
    return LabGlossaryScanJobResponse(
        id=j.id,
        scan_type=j.scan_type,
        scan_input=j.scan_input,
        status=j.status,
        proposed_new_count=j.proposed_new_count,
        proposed_changed_count=j.proposed_changed_count,
        accepted_count=j.accepted_count,
        rejected_count=j.rejected_count,
        error_message=j.error_message,
        created_at=j.created_at,
        completed_at=j.completed_at,
    )


def _proposal_response(p, existing_definition: str | None = None) -> LabGlossaryProposalResponse:
    return LabGlossaryProposalResponse(
        id=p.id,
        term=p.term,
        proposed_definition=p.proposed_definition,
        proposed_aliases=p.proposed_aliases,
        proposed_category=p.proposed_category,
        proposed_context=p.proposed_context,
        proposal_type=p.proposal_type,
        existing_term_id=p.existing_term_id,
        existing_definition=existing_definition,
        source_description=p.source_description,
        previously_rejected=p.previously_rejected,
        review_status=p.review_status,
    )


# --- terms -------------------------------------------------------------------


@router.get("/glossary", response_model=LabGlossaryTermListResponse)
async def list_terms(
    category: str | None = Query(default=None),
    source: str | None = Query(default=None),
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = require_permission("lab_glossary", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    terms, total = await LabGlossaryService.list_terms(
        session, org_id=org_id, category=category, source=source, query=q, page=page, page_size=page_size
    )
    return LabGlossaryTermListResponse(
        terms=[_term_response(t) for t in terms], total=total, page=page, page_size=page_size
    )


@router.post("/glossary", response_model=LabGlossaryTermResponse)
async def create_term(
    body: LabGlossaryTermCreate,
    current_user: dict = require_permission("lab_glossary", "manage"),
    session: AsyncSession = Depends(get_session),
):
    org_id, user_id = int(current_user["org_id"]), int(current_user["sub"])
    try:
        term = await LabGlossaryService.create_term(
            session,
            org_id=org_id,
            user_id=user_id,
            term=body.term,
            definition=body.definition,
            aliases=body.aliases,
            category=body.category,
            context=body.context,
        )
    except DuplicateTermError as exc:
        raise HTTPException(409, {"error": "duplicate_term", "existing_term_id": exc.existing_term_id})
    await session.commit()
    term = await LabGlossaryService.get_term(session, term_id=term.id, org_id=org_id)
    return _term_response(term)


@router.get("/glossary/pending", response_model=LabGlossaryPendingResponse)
async def pending_review(
    current_user: dict = require_permission("lab_glossary", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    count = await scan_svc.pending_review_count(session, org_id=org_id)
    job_ids = await scan_svc.pending_review_job_ids(session, org_id=org_id)
    return LabGlossaryPendingResponse(pending_review_count=count, job_ids=job_ids)


@router.get("/glossary/{term_id}", response_model=LabGlossaryTermResponse)
async def get_term(
    term_id: int,
    current_user: dict = require_permission("lab_glossary", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    term = await LabGlossaryService.get_term(session, term_id=term_id, org_id=org_id)
    if term is None:
        raise HTTPException(404, "Term not found")
    return _term_response(term)


@router.patch("/glossary/{term_id}", response_model=LabGlossaryTermResponse)
async def update_term(
    term_id: int,
    body: LabGlossaryTermUpdate,
    current_user: dict = require_permission("lab_glossary", "manage"),
    session: AsyncSession = Depends(get_session),
):
    org_id, user_id = int(current_user["org_id"]), int(current_user["sub"])
    try:
        term = await LabGlossaryService.update_term(
            session,
            org_id=org_id,
            user_id=user_id,
            term_id=term_id,
            term=body.term,
            definition=body.definition,
            aliases=body.aliases,
            category=body.category,
            context=body.context,
        )
    except DuplicateTermError as exc:
        raise HTTPException(409, {"error": "duplicate_term", "existing_term_id": exc.existing_term_id})
    if term is None:
        raise HTTPException(404, "Term not found")
    await session.commit()
    term = await LabGlossaryService.get_term(session, term_id=term_id, org_id=org_id)
    return _term_response(term)


@router.delete("/glossary/{term_id}")
async def delete_term(
    term_id: int,
    current_user: dict = require_permission("lab_glossary", "delete"),
    session: AsyncSession = Depends(get_session),
):
    org_id, user_id = int(current_user["org_id"]), int(current_user["sub"])
    deleted = await LabGlossaryService.delete_term(session, org_id=org_id, user_id=user_id, term_id=term_id)
    if not deleted:
        raise HTTPException(404, "Term not found")
    await session.commit()
    return {"status": "deleted"}


# --- scan / import / review --------------------------------------------------


@router.post("/glossary/scan", response_model=LabGlossaryScanJobResponse)
async def start_scan(
    body: LabGlossaryScanRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = require_permission("lab_glossary", "manage"),
    session: AsyncSession = Depends(get_session),
):
    org_id, user_id = int(current_user["org_id"]), int(current_user["sub"])
    try:
        job = await scan_svc.create_scan_job(
            session, org_id=org_id, user_id=user_id, scan_type=body.scan_type, scan_input=body.scan_input
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    await session.commit()
    background_tasks.add_task(scan_svc.execute_scan, async_session_factory, job_id=job.id)
    return _job_response(job)


@router.post("/glossary/import", response_model=LabGlossaryScanJobResponse)
async def import_csv(
    file: UploadFile,
    current_user: dict = require_permission("lab_glossary", "manage"),
    session: AsyncSession = Depends(get_session),
):
    org_id, user_id = int(current_user["org_id"]), int(current_user["sub"])
    raw = await file.read()
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(400, "File must be UTF-8 encoded text")
    try:
        job = await scan_svc.parse_csv_import(session, org_id=org_id, user_id=user_id, content=content)
    except scan_svc.CsvParseError as exc:
        raise HTTPException(400, {"error": "csv_parse_error", "detail": str(exc)})
    await session.commit()
    return _job_response(job)


@router.get("/glossary/scan/{job_id}", response_model=LabGlossaryScanJobResponse)
async def get_scan_job(
    job_id: int,
    current_user: dict = require_permission("lab_glossary", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    job = await scan_svc.get_job(session, org_id=org_id, job_id=job_id)
    if job is None:
        raise HTTPException(404, "Scan job not found")
    return _job_response(job)


@router.get("/glossary/scan/{job_id}/proposals", response_model=LabGlossaryProposalListResponse)
async def get_scan_proposals(
    job_id: int,
    current_user: dict = require_permission("lab_glossary", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    job = await scan_svc.get_job(session, org_id=org_id, job_id=job_id)
    if job is None:
        raise HTTPException(404, "Scan job not found")
    proposals = await scan_svc.list_proposals(session, job_id=job_id)

    # Resolve existing definitions for changed proposals so the review UI can show
    # the current-vs-proposed comparison.
    existing_defs: dict[int, str] = {}
    for p in proposals:
        if p.existing_term_id is not None:
            existing = await LabGlossaryService.get_term(
                session, term_id=p.existing_term_id, org_id=org_id
            )
            if existing is not None:
                existing_defs[p.existing_term_id] = existing.definition

    new_terms = [_proposal_response(p) for p in proposals if p.proposal_type == "new"]
    changed_terms = [
        _proposal_response(p, existing_defs.get(p.existing_term_id)) for p in proposals if p.proposal_type == "changed"
    ]
    return LabGlossaryProposalListResponse(
        job=_job_response(job), new_terms=new_terms, changed_terms=changed_terms
    )


@router.post("/glossary/scan/{job_id}/review", response_model=LabGlossaryReviewResponse)
async def review_scan(
    job_id: int,
    body: LabGlossaryReviewRequest,
    current_user: dict = require_permission("lab_glossary", "manage"),
    session: AsyncSession = Depends(get_session),
):
    org_id, user_id = int(current_user["org_id"]), int(current_user["sub"])
    decisions = {d.proposal_id: d.decision for d in body.decisions}
    try:
        summary = await scan_svc.review_proposals(
            session,
            org_id=org_id,
            user_id=user_id,
            job_id=job_id,
            decisions=decisions,
            accept_all_remaining=body.accept_all_remaining,
            reject_all_remaining=body.reject_all_remaining,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    await session.commit()
    return LabGlossaryReviewResponse(**summary)
