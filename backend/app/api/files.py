from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File as FastAPIFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_role
from app.database import get_session
from app.schemas.experiment import UserSummary
from app.schemas.file import (
    FileListResponse,
    FileLinkRequest,
    FileResponse,
    FileUploadComplete,
    FileUploadInitiate,
    FileUploadInitiateResponse,
)
from app.services.file_service import FileService
from app.services.upload_service import UploadService

router = APIRouter(prefix="/api/files", tags=["files"])


def _file_response(f) -> FileResponse:
    return FileResponse(
        id=f.id,
        filename=f.filename,
        gcs_uri=f.gcs_uri,
        size_bytes=f.size_bytes,
        md5_checksum=f.md5_checksum,
        file_type=f.file_type,
        tags=f.tags_json if isinstance(f.tags_json, list) else [],
        uploader=UserSummary(id=f.uploader.id, name=f.uploader.name, email=f.uploader.email) if f.uploader else None,
        upload_timestamp=f.upload_timestamp,
        created_at=f.created_at,
    )


@router.post("/upload/initiate", response_model=FileUploadInitiateResponse)
async def initiate_upload(
    body: FileUploadInitiate,
    current_user: dict = require_role("admin", "comp_bio", "bench"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    try:
        result = await UploadService.initiate_upload(
            session,
            org_id,
            user_id,
            filename=body.filename,
            expected_size=body.expected_size_bytes,
            expected_md5=body.expected_md5,
            experiment_id=body.experiment_id,
            sample_ids=body.sample_ids,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return FileUploadInitiateResponse(**result)


@router.post("/upload/complete", response_model=FileResponse)
async def complete_upload(
    body: FileUploadComplete,
    current_user: dict = require_role("admin", "comp_bio", "bench"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    try:
        file = await UploadService.complete_upload(session, org_id, body.upload_id, body.actual_md5)
        await session.commit()
        file = await FileService.get_file(session, file.id, org_id)
        return _file_response(file)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/upload/simple", response_model=FileResponse)
async def simple_upload(
    file: UploadFile = FastAPIFile(...),
    experiment_id: int | None = Query(None),
    current_user: dict = require_role("admin", "comp_bio", "bench"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    try:
        result = await UploadService.simple_upload(
            session,
            org_id,
            user_id,
            file.filename or "unknown",
            file.file,
            size_bytes=file.size,
            experiment_id=experiment_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Upload failed: {e}")
    await session.commit()
    result = await FileService.get_file(session, result.id, org_id)
    return _file_response(result)


@router.get("", response_model=FileListResponse)
async def list_files(
    request: Request,
    file_type: str | None = None,
    experiment_id: int | None = None,
    page: int = 1,
    page_size: int = 25,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    files, total = await FileService.list_files(session, org_id, file_type, experiment_id, page, page_size)
    return FileListResponse(
        files=[_file_response(f) for f in files],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{file_id}", response_model=FileResponse)
async def get_file(
    file_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    file = await FileService.get_file(session, file_id, org_id)
    if not file:
        raise HTTPException(404, "File not found")
    return _file_response(file)


@router.get("/{file_id}/download")
async def download_file(
    file_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    file = await FileService.get_file(session, file_id, org_id)
    if not file:
        raise HTTPException(404, "File not found")

    # Generate signed download URL
    try:
        from google.cloud import storage as gcs_storage

        client = gcs_storage.Client()
        parts = file.gcs_uri.replace("gs://", "").split("/", 1)
        bucket = client.bucket(parts[0])
        blob = bucket.blob(parts[1])
        url = blob.generate_signed_url(version="v4", expiration=3600, method="GET")
        return {"download_url": url}
    except Exception:
        return {"download_url": file.gcs_uri}


@router.delete("/{file_id}")
async def delete_file(
    file_id: int,
    current_user: dict = require_role("admin", "comp_bio"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    deleted = await FileService.delete_file_record(session, file_id, org_id, user_id)
    if not deleted:
        raise HTTPException(404, "File not found")
    await session.commit()
    return {"status": "deleted"}


@router.post("/{file_id}/link")
async def link_file(
    file_id: int,
    body: FileLinkRequest,
    current_user: dict = require_role("admin", "comp_bio", "bench"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])

    file = await FileService.get_file(session, file_id, org_id)
    if not file:
        raise HTTPException(404, "File not found")

    if body.sample_id:
        await FileService.link_file_to_sample(session, file_id, body.sample_id)
    await session.commit()
    return {"status": "linked"}
