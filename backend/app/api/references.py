"""Reference data management API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
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
    ReferenceImportRequest,
    ReferenceImportStartResponse,
    ReferenceImportStatusResponse,
    ReferenceUploadInitRequest,
    ReferenceUploadInitResponse,
    ReferenceUploadSlot,
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


@router.get("/by-name", response_model=ReferenceDatasetListResponse)
async def list_versions_by_name(
    name: str,
    category: str,
    current_user: dict = require_permission("pipelines", "view"),
    session: AsyncSession = Depends(get_session),
):
    """Return every version for a (name, category) tuple, newest first."""
    org_id = int(current_user["org_id"])
    refs, total = await ReferenceDataService.list_versions_by_name(session, org_id, name=name, category=category)
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


@router.post("/upload-init", response_model=ReferenceUploadInitResponse)
async def init_upload(
    payload: ReferenceUploadInitRequest,
    request: Request,
    current_user: dict = require_permission("references", "upload"),
    session: AsyncSession = Depends(get_session),
):
    """Initiate a resumable upload session for a new reference dataset.

    Returns one GCS resumable session URL per declared file. The browser then
    PUTs chunks against those URLs directly.
    """
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    origin = request.headers.get("origin")

    try:
        dataset, uploads = await ReferenceDataService.init_upload(
            session, org_id, user_id, payload, request_origin=origin
        )
        await session.commit()
    except ValueError as e:
        await session.rollback()
        msg = str(e)
        if "already exists" in msg:
            raise HTTPException(409, msg)
        if "not configured" in msg:
            raise HTTPException(503, msg)
        raise HTTPException(400, msg)

    return ReferenceUploadInitResponse(
        reference_id=dataset.id,
        gcs_prefix=dataset.gcs_prefix,
        uploads=[ReferenceUploadSlot(**u) for u in uploads],
    )


@router.post("/import", response_model=ReferenceImportStartResponse)
async def start_import(
    payload: ReferenceImportRequest,
    current_user: dict = require_permission("references", "upload"),
    session: AsyncSession = Depends(get_session),
):
    """Launch a per-import GKE Job to download a reference from a public URL."""
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    try:
        dataset, job_id = await ReferenceDataService.start_import(session, org_id, user_id, payload)
        await session.commit()
    except ValueError as e:
        await session.rollback()
        msg = str(e)
        if "already exists" in msg:
            raise HTTPException(409, msg)
        if "not configured" in msg:
            raise HTTPException(503, msg)
        raise HTTPException(400, msg)

    return ReferenceImportStartResponse(reference_id=dataset.id, import_job_id=job_id, status="pending")


@router.get("/{reference_id}/import-status", response_model=ReferenceImportStatusResponse)
async def get_import_status(
    reference_id: int,
    current_user: dict = require_permission("references", "view"),
    session: AsyncSession = Depends(get_session),
):
    """Read the in-flight import progress row."""
    org_id = int(current_user["org_id"])
    try:
        return await ReferenceDataService.get_import_status(session, reference_id, org_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/{reference_id}/import-cancel", status_code=204)
async def cancel_import(
    reference_id: int,
    current_user: dict = require_permission("references", "upload"),
    session: AsyncSession = Depends(get_session),
):
    """Terminate the GKE job and purge the in-flight reference."""
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    try:
        await ReferenceDataService.cancel_import(session, reference_id, org_id, user_id)
        await session.commit()
    except ValueError as e:
        await session.rollback()
        raise HTTPException(400, str(e))

    return Response(status_code=204)


@router.post("/{reference_id}/upload-complete", response_model=ReferenceDatasetDetailResponse)
async def upload_complete(
    reference_id: int,
    current_user: dict = require_permission("references", "upload"),
    session: AsyncSession = Depends(get_session),
):
    """Finalize a resumable upload — list GCS, verify files, persist md5+size."""
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    try:
        await ReferenceDataService.upload_complete(session, reference_id, org_id, user_id)
        await session.commit()
    except ValueError as e:
        await session.rollback()
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(404, msg)
        if "not configured" in msg.lower():
            raise HTTPException(503, msg)
        raise HTTPException(400, msg)

    dataset = await ReferenceDataService.get_reference(session, reference_id, org_id)
    return _detail_response(dataset)


@router.post("/{reference_id}/abort", status_code=204)
async def abort_upload(
    reference_id: int,
    current_user: dict = require_permission("references", "upload"),
    session: AsyncSession = Depends(get_session),
):
    """Abort an in-flight upload: purge GCS objects and delete the row. Idempotent."""
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    try:
        await ReferenceDataService.abort_upload(session, reference_id, org_id, user_id)
        await session.commit()
    except ValueError as e:
        await session.rollback()
        msg = str(e)
        if "not configured" in msg.lower():
            raise HTTPException(503, msg)
        raise HTTPException(400, msg)

    return Response(status_code=204)


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
