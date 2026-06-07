from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File as FastAPIFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.schemas.data_search import DataSearchItem, DataSearchResponse
from app.schemas.experiment import UserSummary
from app.schemas.file import (
    FileListResponse,
    FileLinkRequest,
    FileProvenance,
    FileResponse,
    FileUploadComplete,
    FileUploadInitiate,
    FileUploadInitiateResponse,
)
from app.services.data_search_service import unified_document_file_search
from app.services.file_service import FileService
from app.services.upload_service import UploadService

router = APIRouter(prefix="/api/files", tags=["files"])


def _scope_ok(user: dict, resource: str, action: str) -> bool:
    """API-key requests are narrowed to the key's scope envelope (ADR-049); JWT
    requests (api_key_id None) are not. Mirrors ``search.py::_scope_ok``."""
    if user.get("api_key_id") is None:
        return True
    return f"{resource}:{action}" in (user.get("scopes") or [])


def _file_response(f, sample_ids: list[int] | None = None, provenance: dict | None = None) -> FileResponse:
    return FileResponse(
        id=f.id,
        filename=f.filename,
        gcs_uri=f.gcs_uri,
        size_bytes=f.size_bytes,
        md5_checksum=f.md5_checksum,
        file_type=f.file_type,
        tags=f.tags_json if isinstance(f.tags_json, list) else [],
        uploader=UserSummary(id=f.uploader.id, name=f.uploader.name, email=f.uploader.email) if f.uploader else None,
        project_id=f.project_id,
        experiment_id=f.experiment_id,
        sample_ids=sample_ids or [],
        source_type=f.source_type,
        source_pipeline_run_id=f.source_pipeline_run_id,
        source_notebook_session_id=f.source_notebook_session_id,
        storage_deleted=f.storage_deleted,
        is_global=f.is_global,
        upload_timestamp=f.upload_timestamp,
        created_at=f.created_at,
        provenance=FileProvenance.model_validate(provenance) if provenance else None,
    )


@router.post("/upload/initiate", response_model=FileUploadInitiateResponse)
async def initiate_upload(
    body: FileUploadInitiate,
    current_user: dict = require_permission("files", "upload"),
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
            project_id=body.project_id,
            experiment_id=body.experiment_id,
            sample_ids=body.sample_ids,
            is_global=body.is_global,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return FileUploadInitiateResponse(**result)


@router.post("/upload/complete", response_model=FileResponse)
async def complete_upload(
    body: FileUploadComplete,
    current_user: dict = require_permission("files", "upload"),
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
    current_user: dict = require_permission("files", "upload"),
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
    current_user: dict = require_permission("files", "edit"),
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
                "AND storage_uri LIKE :pattern"
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
                "AND storage_uri LIKE :pattern"
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
    project_id: int | None = None,
    source_type: str | None = None,
    sample_id: int | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 25,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    files, total = await FileService.list_files(
        session, org_id, file_type, experiment_id, project_id, source_type, sample_id, search, page, page_size
    )
    file_ids = [f.id for f in files]
    sample_ids_map = await FileService.get_sample_ids_for_files(session, file_ids)
    provenance_map = await FileService.get_provenance_for_files(session, files)
    return FileListResponse(
        files=[_file_response(f, sample_ids_map.get(f.id, []), provenance_map.get(f.id)) for f in files],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/search", response_model=DataSearchResponse)
async def data_search(
    request: Request,
    q: str = "",
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """Unified text search across Data & Files files AND Lab Knowledge documents.

    A lab document is still a file-like thing, so a search here surfaces it too
    (LK-SPEC-D, D3). Each store is gated independently on the caller's view
    permission: a viewer without ``lab_documents:view`` gets files only, and vice
    versa, with no 403 dead-end (mirrors the global-search permission model)."""
    from app.services import role_service

    current_user = request.state.current_user
    org_id = int(current_user["org_id"])
    if not q.strip():
        return DataSearchResponse(items=[])
    if "role_id" not in current_user:
        return DataSearchResponse(items=[])
    role_id = int(current_user["role_id"])

    include_files = await role_service.has_permission(session, role_id, "files", "view") and _scope_ok(
        current_user, "files", "view"
    )
    include_lab_documents = await role_service.has_permission(session, role_id, "lab_documents", "view") and _scope_ok(
        current_user, "lab_documents", "view"
    )

    items = await unified_document_file_search(
        session,
        org_id=org_id,
        query=q,
        include_files=include_files,
        include_lab_documents=include_lab_documents,
        limit=limit,
    )
    return DataSearchResponse(items=[DataSearchItem(**i) for i in items])


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
    sample_ids_map = await FileService.get_sample_ids_for_files(session, [file_id])
    provenance_map = await FileService.get_provenance_for_files(session, [file])
    return _file_response(file, sample_ids_map.get(file_id, []), provenance_map.get(file_id))


@router.get("/{file_id}/download")
async def download_file(
    file_id: int,
    current_user: dict = require_permission("files", "download"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    file = await FileService.get_file(session, file_id, org_id)
    if not file:
        raise HTTPException(404, "File not found")
    if file.storage_deleted:
        raise HTTPException(410, "File storage has been deleted")

    # Generate signed download URL
    try:
        from app.adapters.registry import get_storage_adapter

        url = await get_storage_adapter().generate_signed_url(
            file.gcs_uri, method="GET", expiry_seconds=3600
        )
    except Exception:
        raise HTTPException(502, "Could not generate download URL")

    # Audit log the download
    from app.services.audit_service import log_action

    await log_action(
        session,
        user_id=user_id,
        entity_type="file",
        entity_id=file.id,
        action="download",
        details={
            "filename": file.filename,
            "file_type": file.file_type,
            "size_bytes": file.size_bytes,
            "method": "signed_url",
        },
    )
    await session.commit()

    return {"download_url": url}


@router.get("/{file_id}/content")
async def file_content(
    file_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Serve file bytes directly (same-origin proxy for cross-origin GCS content).

    Accepts either a Bearer session JWT (Authorization header) or a
    short-lived content token (query param). Content tokens carry org_id
    but no user identity.
    """
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    file = await FileService.get_file(session, file_id, org_id)
    if not file:
        raise HTTPException(404, "File not found")
    if file.storage_deleted:
        raise HTTPException(410, "File storage has been deleted")

    try:
        from app.adapters.registry import get_storage_adapter

        data = await get_storage_adapter().read_bytes(file.gcs_uri)

        content_type = "application/octet-stream"
        if file.filename.endswith(".png"):
            content_type = "image/png"
        elif file.filename.endswith(".jpg") or file.filename.endswith(".jpeg"):
            content_type = "image/jpeg"
        elif file.filename.endswith(".svg"):
            content_type = "image/svg+xml"
        elif file.filename.endswith(".pdf"):
            content_type = "application/pdf"

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
    current_user: dict = require_permission("files", "delete"),
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
    current_user: dict = require_permission("files", "edit"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    file = await FileService.get_file(session, file_id, org_id)
    if not file:
        raise HTTPException(404, "File not found")

    if body.project_id is not None:
        file.project_id = body.project_id

    if body.experiment_id is not None:
        if file.experiment_id != body.experiment_id:
            from app.services.file_organization import FileOrganizationService

            await FileOrganizationService.assign_file_to_experiment(session, file_id, body.experiment_id, user_id)

        # Auto-transition experiment status for FASTQ files
        if file.file_type == "fastq":
            await UploadService._auto_update_experiment_status(session, body.experiment_id, org_id, user_id)

    if body.sample_id:
        await FileService.link_file_to_sample(session, file_id, body.sample_id)
        from app.services.audit_service import log_action

        await log_action(
            session,
            user_id=user_id,
            entity_type="file",
            entity_id=file_id,
            action="linked_to_sample",
            details={"sample_id": body.sample_id, "filename": file.filename},
        )
    await session.commit()
    return {"status": "linked"}
