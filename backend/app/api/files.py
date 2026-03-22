from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File as FastAPIFile
from fastapi.responses import Response
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
        experiment_id=f.experiment_id,
        source_type=f.source_type,
        source_pipeline_run_id=f.source_pipeline_run_id,
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


@router.post("/reconcile")
async def reconcile_stuck_files(
    current_user: dict = require_role("admin"),
    session: AsyncSession = Depends(get_session),
):
    """Move files stuck in the ingest bucket to the raw bucket.

    Finds files that have an experiment_id but whose gcs_uri still points
    to the ingest bucket, then moves each one to the raw bucket under the
    correct experiment prefix. Also advances experiment status for any
    experiments that have FASTQ files reconciled.
    """
    import logging

    from sqlalchemy import text

    from app.services.file_organization import FileOrganizationService

    logger = logging.getLogger("bioaf.files")
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    # Read bucket names
    config_rows = (
        await session.execute(
            text("SELECT key, value FROM platform_config WHERE key IN ('ingest_bucket_name', 'raw_bucket_name')")
        )
    ).fetchall()
    config = {r[0]: r[1] for r in config_rows}
    ingest_bucket = config.get("ingest_bucket_name", "")
    raw_bucket = config.get("raw_bucket_name", "")

    if not ingest_bucket or not raw_bucket:
        raise HTTPException(400, "Ingest or raw bucket not configured")

    # Find stuck files: have experiment_id, URI still in ingest bucket
    stuck = (
        await session.execute(
            text(
                "SELECT id, experiment_id, file_type FROM files "
                "WHERE organization_id = :org_id "
                "AND experiment_id IS NOT NULL "
                "AND gcs_uri LIKE :pattern"
            ).bindparams(org_id=org_id, pattern=f"gs://{ingest_bucket}/%")
        )
    ).fetchall()

    reconciled = 0
    failed = 0
    experiments_with_fastq: set[int] = set()

    for file_id, experiment_id, file_type in stuck:
        try:
            await FileOrganizationService.assign_file_to_experiment(session, file_id, experiment_id, user_id)
            reconciled += 1
            if file_type == "fastq":
                experiments_with_fastq.add(experiment_id)
        except Exception as e:
            logger.warning("Failed to reconcile file %d: %s", file_id, e)
            failed += 1

    # Advance experiment status for any with reconciled FASTQs
    for exp_id in experiments_with_fastq:
        await UploadService._auto_update_experiment_status(session, exp_id, org_id, user_id)

    await session.commit()

    # Count files already in raw bucket (skipped)
    already_ok = (
        await session.execute(
            text(
                "SELECT COUNT(*) FROM files "
                "WHERE organization_id = :org_id "
                "AND experiment_id IS NOT NULL "
                "AND gcs_uri LIKE :pattern"
            ).bindparams(org_id=org_id, pattern=f"gs://{raw_bucket}/%")
        )
    ).scalar_one()

    return {
        "reconciled": reconciled,
        "failed": failed,
        "skipped": already_ok,
    }


@router.get("/stats")
async def file_stats(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Return file counts grouped by source (artifacts vs uploaded) and file type."""
    from sqlalchemy import case, func, select

    from app.models.file import File

    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    is_artifact = case(
        (File.source_type != "upload", "artifacts"),
        else_="uploaded",
    )

    rows = (
        await session.execute(
            select(
                is_artifact.label("source"),
                func.coalesce(File.file_type, "unknown").label("ftype"),
                func.count().label("cnt"),
            )
            .where(File.organization_id == org_id)
            .group_by("source", "ftype")
        )
    ).all()

    artifacts: dict[str, int] = {}
    uploaded: dict[str, int] = {}
    for source, ftype, cnt in rows:
        bucket = artifacts if source == "artifacts" else uploaded
        bucket[ftype] = cnt

    return {
        "artifacts": {
            "total": sum(artifacts.values()),
            "by_type": dict(sorted(artifacts.items(), key=lambda x: -x[1])),
        },
        "uploaded": {
            "total": sum(uploaded.values()),
            "by_type": dict(sorted(uploaded.items(), key=lambda x: -x[1])),
        },
    }


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

        from app.services.gcs_storage import GcsStorageService

        credentials = await GcsStorageService.get_credentials(session)
        client = gcs_storage.Client(credentials=credentials)
        parts = file.gcs_uri.replace("gs://", "").split("/", 1)
        bucket = client.bucket(parts[0])
        blob = bucket.blob(parts[1])
        url = blob.generate_signed_url(version="v4", expiration=3600, method="GET")
        return {"download_url": url}
    except Exception:
        raise HTTPException(502, "Could not generate download URL")


@router.get("/{file_id}/content")
async def file_content(
    file_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Serve file bytes directly (same-origin proxy for cross-origin GCS content)."""
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    file = await FileService.get_file(session, file_id, org_id)
    if not file:
        raise HTTPException(404, "File not found")

    try:
        from google.cloud import storage as gcs_storage

        from app.services.gcs_storage import GcsStorageService

        credentials = await GcsStorageService.get_credentials(session)
        client = gcs_storage.Client(credentials=credentials)
        parts = file.gcs_uri.replace("gs://", "").split("/", 1)
        bucket = client.bucket(parts[0])
        blob = bucket.blob(parts[1])
        data = blob.download_as_bytes()

        content_type = "application/octet-stream"
        if file.filename.endswith(".png"):
            content_type = "image/png"
        elif file.filename.endswith(".jpg") or file.filename.endswith(".jpeg"):
            content_type = "image/jpeg"

        return Response(
            content=data,
            media_type=content_type,
            headers={"Cache-Control": "public, max-age=3600"},
        )
    except Exception:
        raise HTTPException(502, "Could not fetch file content")


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
    user_id = int(current_user["sub"])

    file = await FileService.get_file(session, file_id, org_id)
    if not file:
        raise HTTPException(404, "File not found")

    if body.experiment_id is not None:
        from app.services.file_organization import FileOrganizationService

        await FileOrganizationService.assign_file_to_experiment(session, file_id, body.experiment_id, user_id)

        # Auto-transition experiment status for FASTQ files
        if file.file_type == "fastq":
            await UploadService._auto_update_experiment_status(session, body.experiment_id, org_id, user_id)

    if body.sample_id:
        await FileService.link_file_to_sample(session, file_id, body.sample_id)
    await session.commit()
    return {"status": "linked"}
