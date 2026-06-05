"""SDR API (ADR-063, ADR-064). Endpoints under /api/lab-knowledge.

SDR CRUD, the guarded status-machine transition endpoint, owner reassignment,
and category management. Service-layer guard exceptions (ADR-063) map to HTTP
422 so an invalid transition is rejected regardless of caller. ``sdr:author``
covers create + the author-allowed transitions (draft->active,
active->flagged_for_review, flagged->active); ``sdr:manage`` covers all
transitions, delete, owner reassignment, and categories.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_any_permission, require_permission
from app.database import get_session
from app.schemas.experiment import UserSummary
from app.schemas.sdr import (
    SdrCategoryCreate,
    SdrCategoryResponse,
    SdrCreate,
    SdrDetailResponse,
    SdrListResponse,
    SdrOwnerReassignRequest,
    SdrSummary,
    SdrSupersessionLink,
    SdrTransitionRequest,
    SdrTransitionResponse,
    SdrUpdate,
)
from app.services import role_service
from app.services.sdr_service import (
    CategoryInUseError,
    InvalidTransitionError,
    SdrReadOnlyError,
    SdrService,
    SupersededByRequiredError,
    TransitionNoteRequiredError,
)

router = APIRouter(prefix="/api/lab-knowledge", tags=["lab-knowledge"])

# Transitions reserved to sdr:manage (the terminal/destructive ones). All other
# transitions are allowed for sdr:author or sdr:manage (ADR-063 transition table).
MANAGE_ONLY_TARGETS = {"superseded", "repealed"}


def _user_summary(user) -> UserSummary | None:
    if user is None:
        return None
    return UserSummary(id=user.id, name=user.name, email=user.email)


def _category_response(c) -> SdrCategoryResponse | None:
    if c is None:
        return None
    return SdrCategoryResponse(id=c.id, name=c.name)


def _link(s) -> SdrSupersessionLink | None:
    if s is None:
        return None
    return SdrSupersessionLink(id=s.id, sdr_number=s.sdr_number, title=s.title, status=s.status)


def _summary(s) -> SdrSummary:
    return SdrSummary(
        id=s.id,
        sdr_number=s.sdr_number,
        title=s.title,
        status=s.status,
        category=_category_response(getattr(s, "category", None)),
        owner=_user_summary(getattr(s, "owner", None)),
        trigger_date=s.trigger_date,
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


def _detail(s) -> SdrDetailResponse:
    return SdrDetailResponse(
        id=s.id,
        sdr_number=s.sdr_number,
        title=s.title,
        status=s.status,
        category=_category_response(getattr(s, "category", None)),
        owner=_user_summary(getattr(s, "owner", None)),
        trigger_date=s.trigger_date,
        created_at=s.created_at,
        updated_at=s.updated_at,
        decision=s.decision,
        justification=s.justification,
        created_by=_user_summary(getattr(s, "created_by", None)),
        trigger_warning_sent_at=s.trigger_warning_sent_at,
        superseded_by=_link(getattr(s, "superseded_by", None)),
        supersedes=_link(getattr(s, "supersedes", None)),
        transitions=[
            SdrTransitionResponse(
                id=t.id,
                from_status=t.from_status,
                to_status=t.to_status,
                note=t.note,
                transitioned_by=_user_summary(getattr(t, "transitioned_by", None)),
                transitioned_at=t.transitioned_at,
            )
            for t in sorted(getattr(s, "transitions", []), key=lambda t: t.id)
        ],
    )


# --- categories --------------------------------------------------------------


@router.get("/sdr-categories", response_model=list[SdrCategoryResponse])
async def list_categories(
    current_user: dict = require_permission("sdr", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    cats = await SdrService.list_categories(session, org_id=org_id)
    return [_category_response(c) for c in cats]


@router.post("/sdr-categories", response_model=SdrCategoryResponse)
async def create_category(
    body: SdrCategoryCreate,
    current_user: dict = require_permission("sdr", "manage"),
    session: AsyncSession = Depends(get_session),
):
    org_id, user_id = int(current_user["org_id"]), int(current_user["sub"])
    cat = await SdrService.create_category(session, org_id=org_id, user_id=user_id, name=body.name)
    await session.commit()
    return _category_response(cat)


@router.delete("/sdr-categories/{category_id}")
async def delete_category(
    category_id: int,
    current_user: dict = require_permission("sdr", "manage"),
    session: AsyncSession = Depends(get_session),
):
    org_id, user_id = int(current_user["org_id"]), int(current_user["sub"])
    try:
        deleted = await SdrService.delete_category(
            session, org_id=org_id, user_id=user_id, category_id=category_id
        )
    except CategoryInUseError as exc:
        raise HTTPException(409, {"error": "category_in_use", "detail": str(exc)})
    if not deleted:
        raise HTTPException(404, "Category not found")
    await session.commit()
    return {"status": "deleted"}


# --- SDRs --------------------------------------------------------------------


@router.get("/sdrs", response_model=SdrListResponse)
async def list_sdrs(
    status: list[str] | None = Query(default=None),
    category_id: list[int] | None = Query(default=None),
    owner_user_id: int | None = Query(default=None),
    q: str | None = Query(default=None),
    include_historical: bool = Query(default=False),
    sort: str = Query(default="number"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = require_permission("sdr", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    rows, total = await SdrService.list_sdrs(
        session,
        org_id=org_id,
        statuses=status,
        category_ids=category_id,
        owner_user_id=owner_user_id,
        query=q,
        include_historical=include_historical,
        sort=sort,
        page=page,
        page_size=page_size,
    )
    return SdrListResponse(
        sdrs=[_summary(s) for s in rows], total=total, page=page, page_size=page_size
    )


@router.post("/sdrs", response_model=SdrDetailResponse)
async def create_sdr(
    body: SdrCreate,
    current_user: dict = require_permission("sdr", "author"),
    session: AsyncSession = Depends(get_session),
):
    org_id, user_id = int(current_user["org_id"]), int(current_user["sub"])
    sdr = await SdrService.create_sdr(
        session,
        org_id=org_id,
        user_id=user_id,
        title=body.title,
        decision=body.decision,
        justification=body.justification,
        category_id=body.category_id,
        trigger_date=body.trigger_date,
    )
    await session.commit()
    sdr = await SdrService.get_sdr(session, sdr_id=sdr.id, org_id=org_id)
    return _detail(sdr)


@router.get("/sdrs/{sdr_id}", response_model=SdrDetailResponse)
async def get_sdr(
    sdr_id: int,
    current_user: dict = require_permission("sdr", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    sdr = await SdrService.get_sdr(session, sdr_id=sdr_id, org_id=org_id)
    if sdr is None:
        raise HTTPException(404, "SDR not found")
    return _detail(sdr)


@router.patch("/sdrs/{sdr_id}", response_model=SdrDetailResponse)
async def update_sdr(
    sdr_id: int,
    body: SdrUpdate,
    current_user: dict = require_any_permission([("sdr", "author"), ("sdr", "manage")]),
    session: AsyncSession = Depends(get_session),
):
    org_id, user_id = int(current_user["org_id"]), int(current_user["sub"])
    fields = body.model_fields_set
    try:
        sdr = await SdrService.update_sdr(
            session,
            org_id=org_id,
            user_id=user_id,
            sdr_id=sdr_id,
            title=body.title,
            category_id=body.category_id,
            category_id_set="category_id" in fields,
            decision=body.decision,
            justification=body.justification,
            trigger_date=body.trigger_date,
            trigger_date_set="trigger_date" in fields,
        )
    except SdrReadOnlyError as exc:
        raise HTTPException(422, str(exc))
    if sdr is None:
        raise HTTPException(404, "SDR not found")
    await session.commit()
    sdr = await SdrService.get_sdr(session, sdr_id=sdr_id, org_id=org_id)
    return _detail(sdr)


@router.delete("/sdrs/{sdr_id}")
async def delete_sdr(
    sdr_id: int,
    current_user: dict = require_permission("sdr", "manage"),
    session: AsyncSession = Depends(get_session),
):
    org_id, user_id = int(current_user["org_id"]), int(current_user["sub"])
    deleted = await SdrService.delete_sdr(session, org_id=org_id, user_id=user_id, sdr_id=sdr_id)
    if not deleted:
        raise HTTPException(404, "SDR not found")
    await session.commit()
    return {"status": "deleted"}


@router.post("/sdrs/{sdr_id}/transition", response_model=SdrDetailResponse)
async def transition_sdr(
    sdr_id: int,
    body: SdrTransitionRequest,
    current_user: dict = require_any_permission([("sdr", "author"), ("sdr", "manage")]),
    session: AsyncSession = Depends(get_session),
):
    org_id, user_id = int(current_user["org_id"]), int(current_user["sub"])
    # Terminal transitions (superseded/repealed) require sdr:manage.
    if body.to_status in MANAGE_ONLY_TARGETS:
        role_id = int(current_user["role_id"])
        if not await role_service.has_permission(session, role_id, "sdr", "manage"):
            raise HTTPException(403, "role_missing")
    try:
        sdr = await SdrService.transition(
            session,
            org_id=org_id,
            user_id=user_id,
            sdr_id=sdr_id,
            to_status=body.to_status,
            note=body.note,
            superseded_by_sdr_id=body.superseded_by_sdr_id,
        )
    except (InvalidTransitionError, TransitionNoteRequiredError, SupersededByRequiredError) as exc:
        raise HTTPException(422, str(exc))
    if sdr is None:
        raise HTTPException(404, "SDR not found")
    await session.commit()
    sdr = await SdrService.get_sdr(session, sdr_id=sdr_id, org_id=org_id)
    return _detail(sdr)


@router.patch("/sdrs/{sdr_id}/owner", response_model=SdrDetailResponse)
async def reassign_owner(
    sdr_id: int,
    body: SdrOwnerReassignRequest,
    current_user: dict = require_permission("sdr", "manage"),
    session: AsyncSession = Depends(get_session),
):
    org_id, user_id = int(current_user["org_id"]), int(current_user["sub"])
    sdr = await SdrService.reassign_owner(
        session, org_id=org_id, user_id=user_id, sdr_id=sdr_id, new_owner_user_id=body.owner_user_id
    )
    if sdr is None:
        raise HTTPException(404, "SDR not found")
    await session.commit()
    sdr = await SdrService.get_sdr(session, sdr_id=sdr_id, org_id=org_id)
    return _detail(sdr)
