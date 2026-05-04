"""Internal callback endpoints used by the reference-import GKE container.

Authenticated by `X-Internal-Token` matching settings.internal_token (rotated,
not user-scoped). The importer container has no user identity.
"""

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session
from app.schemas.reference_dataset import ReferenceImportProgressUpdate
from app.services.reference_data_service import ReferenceDataService

router = APIRouter(prefix="/api/internal/references", tags=["internal"])


def _require_internal_token(x_internal_token: str | None = Header(default=None)) -> None:
    expected = settings.internal_token
    if not expected or not x_internal_token or x_internal_token != expected:
        raise HTTPException(401, "Invalid or missing internal token")


@router.post("/{reference_id}/import-progress")
async def report_import_progress(
    reference_id: int,
    payload: ReferenceImportProgressUpdate,
    _auth: None = Depends(_require_internal_token),
    session: AsyncSession = Depends(get_session),
):
    """Update the import-progress row. Called by the importer container."""
    try:
        await ReferenceDataService.record_import_progress(
            session,
            reference_id=reference_id,
            status=payload.status,
            progress_pct=payload.progress_pct,
            bytes_downloaded=payload.bytes_downloaded,
            total_bytes=payload.total_bytes,
            error_message=payload.error_message,
        )
        await session.commit()
    except ValueError as e:
        await session.rollback()
        raise HTTPException(404, str(e))

    return {"ok": True}
