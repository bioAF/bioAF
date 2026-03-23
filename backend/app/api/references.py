"""Reference data management API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.schemas.reference_dataset import (
    ImpactSummary,
    ReferenceDatasetCreate,
    ReferenceDatasetDetailResponse,
    ReferenceDatasetListResponse,
    ReferenceDatasetResponse,
    ReferenceDeprecateRequest,
)
from app.services.reference_data_service import ReferenceDataService

router = APIRouter(prefix="/api/references", tags=["references"])


def _response(dataset) -> ReferenceDatasetResponse:
    return ReferenceDatasetResponse.model_validate(dataset)


def _detail_response(dataset) -> ReferenceDatasetDetailResponse:
    from app.schemas.reference_dataset import ReferenceDatasetFileResponse, UserSummary

    files = [ReferenceDatasetFileResponse.model_validate(f) for f in (dataset.files or [])]
    uploaded_by = UserSummary.model_validate(dataset.uploaded_by) if dataset.uploaded_by else None
    approved_by = UserSummary.model_validate(dataset.approved_by) if dataset.approved_by else None

    return ReferenceDatasetDetailResponse(
        **ReferenceDatasetResponse.model_validate(dataset).model_dump(),
        files=files,
        uploaded_by=uploaded_by,
        approved_by=approved_by,
    )


@router.get("", response_model=ReferenceDatasetListResponse)
async def list_references(
    category: str | None = None,
    scope: str | None = None,
    status: str | None = None,
    name_search: str | None = None,
    current_user: dict = require_permission("pipelines", "view"),
    session: AsyncSession = Depends(get_session),
):
    """List reference datasets with optional filters."""
    org_id = int(current_user["org_id"])
    refs, total = await ReferenceDataService.list_references(
        session, org_id, category=category, scope=scope, status=status, name_search=name_search
    )
    return ReferenceDatasetListResponse(
        references=[_response(r) for r in refs],
        total=total,
    )


@router.get("/{reference_id}", response_model=ReferenceDatasetDetailResponse)
async def get_reference(
    reference_id: int,
    current_user: dict = require_permission("pipelines", "view"),
    session: AsyncSession = Depends(get_session),
):
    """Get reference dataset detail with file manifest."""
    org_id = int(current_user["org_id"])
    dataset = await ReferenceDataService.get_reference(session, reference_id, org_id)
    if not dataset:
        raise HTTPException(404, "Reference dataset not found")
    return _detail_response(dataset)


@router.post("", response_model=ReferenceDatasetDetailResponse, status_code=201)
async def create_reference(
    data: ReferenceDatasetCreate,
    current_user: dict = require_permission("pipelines", "create"),
    session: AsyncSession = Depends(get_session),
):
    """Create a new reference dataset with file manifest."""
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    try:
        dataset = await ReferenceDataService.create_reference(session, org_id, user_id, data)
        await session.commit()
    except Exception as e:
        await session.rollback()
        # Check for unique constraint violation
        if "uq_reference_org_name_version" in str(e):
            raise HTTPException(409, f"Reference dataset '{data.name}' version '{data.version}' already exists")
        raise HTTPException(400, str(e))

    # Reload with relationships
    dataset = await ReferenceDataService.get_reference(session, dataset.id, org_id)
    return _detail_response(dataset)


@router.post("/{reference_id}/deprecate", response_model=ReferenceDatasetResponse)
async def deprecate_reference(
    reference_id: int,
    data: ReferenceDeprecateRequest,
    current_user: dict = require_permission("pipelines", "create"),
    session: AsyncSession = Depends(get_session),
):
    """Deprecate a reference dataset. Public scope enters pending_approval."""
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    try:
        dataset = await ReferenceDataService.deprecate_reference(session, reference_id, org_id, user_id, data)
        await session.commit()
    except ValueError as e:
        raise HTTPException(400, str(e))

    return _response(dataset)


@router.post("/{reference_id}/approve-deprecation", response_model=ReferenceDatasetResponse)
async def approve_deprecation(
    reference_id: int,
    current_user: dict = require_permission("infrastructure", "configure"),
    session: AsyncSession = Depends(get_session),
):
    """Admin approves a pending public deprecation."""
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    try:
        dataset = await ReferenceDataService.approve_deprecation(session, reference_id, org_id, user_id)
        await session.commit()
    except ValueError as e:
        raise HTTPException(400, str(e))

    return _response(dataset)


@router.get("/{reference_id}/impact", response_model=ImpactSummary)
async def get_impact(
    reference_id: int,
    current_user: dict = require_permission("pipelines", "view"),
    session: AsyncSession = Depends(get_session),
):
    """Get impact assessment: which pipeline runs and experiments used this reference."""
    org_id = int(current_user["org_id"])

    try:
        return await ReferenceDataService.get_impact(session, reference_id, org_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
