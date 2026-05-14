"""File metadata endpoints for /api/v1/integrations.

Read-only. gcs_uri is not exposed; bytes do not flow through this surface in
v1. LIMS systems learn about new files via webhooks and read metadata here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.integrations.dependencies import require_api_key_permission
from app.database import get_session
from app.models.file import File
from app.models.sample import sample_files
from app.schemas.integrations.file import FileListOut, FileOut

router = APIRouter(prefix="/files", tags=["Files"])


async def _sample_ids_for(session: AsyncSession, file_ids: list[int]) -> dict[int, list[int]]:
    if not file_ids:
        return {}
    result = await session.execute(
        select(sample_files.c.file_id, sample_files.c.sample_id).where(sample_files.c.file_id.in_(file_ids))
    )
    out: dict[int, list[int]] = {fid: [] for fid in file_ids}
    for file_id, sample_id in result.all():
        out.setdefault(file_id, []).append(sample_id)
    return out


def _file_out(file: File, sample_ids: list[int]) -> FileOut:
    return FileOut(
        id=file.id,
        filename=file.filename,
        size_bytes=file.size_bytes,
        md5_checksum=file.md5_checksum,
        sha256_checksum=file.sha256_checksum,
        file_type=file.file_type,
        source_type=file.source_type,
        project_id=file.project_id,
        experiment_id=file.experiment_id,
        sample_ids=sample_ids,
        tags=file.tags_json,
        created_at=file.created_at,
    )


@router.get("", response_model=FileListOut, summary="List file metadata")
async def list_files(
    user: dict = require_api_key_permission("files", "view"),
    session: AsyncSession = Depends(get_session),
    project_id: int | None = Query(None),
    experiment_id: int | None = Query(None),
    sample_id: int | None = Query(None),
    source_type: str | None = Query(None),
    cursor: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    org_id = int(user["org_id"])
    stmt = select(File).where(File.organization_id == org_id)
    if project_id is not None:
        stmt = stmt.where(File.project_id == project_id)
    if experiment_id is not None:
        stmt = stmt.where(File.experiment_id == experiment_id)
    if source_type is not None:
        stmt = stmt.where(File.source_type == source_type)
    if sample_id is not None:
        stmt = stmt.join(sample_files, sample_files.c.file_id == File.id).where(sample_files.c.sample_id == sample_id)
    if cursor:
        try:
            stmt = stmt.where(File.id < int(cursor))
        except ValueError as e:
            raise HTTPException(400, "invalid_cursor") from e
    stmt = stmt.order_by(File.id.desc()).limit(limit + 1)
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    next_cursor: str | None = None
    if len(rows) > limit:
        next_cursor = str(rows[limit - 1].id)
        rows = rows[:limit]
    sids = await _sample_ids_for(session, [r.id for r in rows])
    return FileListOut(items=[_file_out(r, sids.get(r.id, [])) for r in rows], next_cursor=next_cursor)


@router.get(
    "/{file_id}",
    response_model=FileOut,
    summary="Get file metadata (gcs_uri excluded)",
)
async def get_file(
    file_id: int = Path(..., ge=1),
    user: dict = require_api_key_permission("files", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(user["org_id"])
    result = await session.execute(select(File).where(File.id == file_id, File.organization_id == org_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "file_not_found")
    sids = await _sample_ids_for(session, [row.id])
    return _file_out(row, sids.get(row.id, []))
