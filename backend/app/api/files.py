from collections.abc import Sequence
from datetime import datetime
from typing import Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File as FastAPIFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.pagination import PageNumber, UnboundedPageSize
from app.api.content_access import file_for_content
from app.api.dependencies import has_effective_permission, require_permission
from app.models.upload_checkpoint import UploadCheckpoint
from app.database import get_session
from app.exceptions import DomainError
from app.schemas.data_search import DataSearchItem, DataSearchResponse
from app.schemas.experiment import UserSummary
from app.schemas.file import (
    UploadReceivedNotFiled,
    UploadPartsRequest,
    UploadPartsResponse,
    FileListResponse,
    FileLinkRequest,
    FileProvenance,
    FileResponse,
    FileUploadComplete,
    FileUploadInitiate,
    FileUploadInitiateResponse,
)
from app.services import entity_references
from app.services.data_search_service import unified_document_file_search
from app.services.file_service import FileService
from app.services.upload_service import UploadService, upload_failure

router = APIRouter(prefix="/api/files", tags=["files"])


def _scope_ok(user: dict, resource: str, action: str) -> bool:
    """API-key requests are narrowed to the key's scope envelope (ADR-049); JWT
    requests (api_key_id None) are not. Mirrors ``search.py::_scope_ok``."""
    if user.get("api_key_id") is None:
        return True
    return f"{resource}:{action}" in (user.get("scopes") or [])


def _file_response(
    f,
    sample_ids: list[int] | None = None,
    provenance: dict | None = None,
    identity: dict | None = None,
    verification: dict | None = None,
) -> FileResponse:
    identity = identity or {}
    return FileResponse(
        id=f.id,
        filename=f.filename,
        # Wire field stays ``gcs_uri`` (frontend contract); source is the neutral column.
        gcs_uri=f.storage_uri,
        size_bytes=f.size_bytes,
        md5_checksum=f.md5_checksum,
        file_type=f.file_type,
        tags=f.tags_json if isinstance(f.tags_json, list) else [],
        uploader=UserSummary(id=f.uploader.id, name=f.uploader.name, email=f.uploader.email) if f.uploader else None,
        project_id=f.project_id,
        experiment_id=f.experiment_id,
        sample_ids=identity.get("sample_ids", sample_ids or []),
        source_type=f.source_type,
        source_pipeline_run_id=f.source_pipeline_run_id,
        source_notebook_session_id=f.source_notebook_session_id,
        storage_deleted=f.storage_deleted,
        is_global=f.is_global,
        upload_timestamp=f.upload_timestamp,
        created_at=f.created_at,
        provenance=FileProvenance.model_validate(provenance) if provenance else None,
        external_id=f.external_id,
        project_external_id=identity.get("project_external_id"),
        experiment_external_id=identity.get("experiment_external_id"),
        samples=identity.get("samples", []),
        ready_at=f.ready_at,
        verification=verification,
    )


async def _file_responses(session: AsyncSession, files: list, *, provenance: bool = True) -> list[FileResponse]:
    """Responses for a page of files with batch-loaded identity, provenance and verification.

    Reads persisted state only: it never starts hashing or touches storage."""
    from app.services import entity_references
    from app.services.file_verification_service import FileVerificationService, records_for

    file_ids = [f.id for f in files]
    identity_map = await entity_references.file_identity_map(session, files)
    provenance_map = await FileService.get_provenance_for_files(session, files) if provenance else {}
    records = await records_for(session, file_ids)
    return [
        _file_response(
            f,
            provenance=provenance_map.get(f.id),
            identity=identity_map.get(f.id),
            verification=FileVerificationService.view(f, records.get(f.id)),
        )
        for f in files
    ]


# The bytes are stored but filing is pending: answered 202 with the reference (mockup M12).
_RECEIVED_NOT_FILED: dict = {
    202: {"model": UploadReceivedNotFiled, "description": "Received, not yet filed; bioAF retries automatically."}
}


@router.post("/upload/initiate", response_model=FileUploadInitiateResponse)
async def initiate_upload(
    body: FileUploadInitiate,
    request: Request,
    current_user: dict = require_permission("files", "upload"),
    session: AsyncSession = Depends(get_session),
):
    from app.adapters.registry import require_capability

    require_capability("signed_url_upload")
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

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
        external_id=body.external_id,
        # The browser's origin lets a GCS session accept the cross-origin upload.
        origin=request.headers.get("origin"),
    )
    return FileUploadInitiateResponse(**result)


@router.post("/uploads/{upload_id}/parts", response_model=UploadPartsResponse)
async def upload_part_links(
    upload_id: str,
    body: UploadPartsRequest,
    current_user: dict = require_permission("files", "upload"),
    session: AsyncSession = Depends(get_session),
):
    """Signed links for parts of the caller's own S3 multipart upload."""
    checkpoint = await session.get(UploadCheckpoint, upload_id)
    if (
        checkpoint is None
        or checkpoint.organization_id != int(current_user["org_id"])
        or checkpoint.user_id != int(current_user["sub"])
    ):
        raise HTTPException(404, "Upload not found")
    urls = await UploadService.sign_parts(session, int(current_user["org_id"]), upload_id, body.part_numbers)
    return UploadPartsResponse(urls=urls)


async def _remove_superseded_copy(file_id: int, org_id: int) -> None:
    """After a filing commits, remove the ingest copy it replaced (the periodic pass retries)."""
    from app.services.storage_cleanup import cleanup_after_commit

    try:
        await cleanup_after_commit(file_id, org_id)
    except Exception as exc:
        import logging

        logging.getLogger("bioaf.files").warning("Ingest copy cleanup deferred (error_type=%s)", type(exc).__name__)


@router.post("/upload/complete", response_model=FileResponse, responses=_RECEIVED_NOT_FILED)
async def complete_upload(
    body: FileUploadComplete,
    current_user: dict = require_permission("files", "upload"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    try:
        parts = None
        if body.parts is not None:
            from app.adapters.models import UploadedPart

            parts = [UploadedPart(number=p.number, etag=p.etag) for p in body.parts]
        file = await UploadService.complete_upload(session, org_id, body.upload_id, body.actual_md5, parts)
    except DomainError:
        raise
    except Exception as exc:
        raise upload_failure("completion", exc) from None
    await session.commit()
    await _remove_superseded_copy(file.id, org_id)
    file = await FileService.get_file(session, file.id, org_id)
    return (await _file_responses(session, [file], provenance=False))[0]


@router.post("/upload/simple", response_model=FileResponse, responses=_RECEIVED_NOT_FILED)
async def simple_upload(
    file: UploadFile = FastAPIFile(...),
    experiment_id: int | None = Query(None),
    project_id: int | None = Query(None),
    sample_ids: list[int] | None = Query(None),
    is_global: bool = Query(False),
    external_id: str | None = Query(None, min_length=1, max_length=255),
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
            project_id=project_id,
            experiment_id=experiment_id,
            sample_ids=sample_ids,
            is_global=is_global,
            external_id=external_id,
        )
    except DomainError:
        raise
    except Exception as exc:
        raise upload_failure("registration", exc) from None
    await session.commit()
    await _remove_superseded_copy(result.id, org_id)
    result = await FileService.get_file(session, result.id, org_id)
    return (await _file_responses(session, [result], provenance=False))[0]


# ---------------------------------------------------------------------------
# Uploads received but not yet filed (mockup M12)
# ---------------------------------------------------------------------------


class PendingUpload(BaseModel):
    upload_id: str
    filename: str
    reference: str
    stage: str
    # "retrying": bioAF will try again automatically; "needs_attention": retries ran out;
    # "stopping": cancelled, waiting for storage to confirm nothing more can arrive;
    # "unconfirmed": storage could not confirm the upload stopped (an administrator may release it).
    state: str
    # For "stopping": "cancelled", "interrupted" (the upload failed partway) or "timed_out"
    # (no new bytes for an hour).
    reason: str | None = None
    attempts: int
    next_attempt_at: datetime | None = None
    experiment_id: int | None = None
    experiment_name: str | None = None
    uploaded_by: str | None = None
    uploaded_by_user_id: int | None = None
    created_at: datetime


class PendingUploadsResponse(BaseModel):
    uploads: list[PendingUpload]


async def _can_manage_all_uploads(session: AsyncSession, user: dict) -> bool:
    """Admins and comp_bio (files edit) see and act on everyone's pending uploads (M12-a)."""
    return await has_effective_permission(session, user, "files", "edit")


async def _pending_checkpoint(session: AsyncSession, user: dict, upload_id: str) -> UploadCheckpoint:
    checkpoint = await session.get(UploadCheckpoint, upload_id)
    if checkpoint is None or checkpoint.organization_id != int(user["org_id"]):
        raise HTTPException(404, "Upload not found")
    if checkpoint.user_id != int(user["sub"]) and not await _can_manage_all_uploads(session, user):
        raise HTTPException(404, "Upload not found")
    return checkpoint


@router.get("/uploads/pending", response_model=PendingUploadsResponse)
async def pending_uploads(
    current_user: dict = require_permission("files", "upload"),
    session: AsyncSession = Depends(get_session),
):
    """Uploads that need following: stored but not yet filed, cancelled but not yet confirmed
    stopped, or whose outcome storage could not confirm. The caller's own, or everyone's for
    files edit."""
    from sqlalchemy import and_, or_, select

    from app.services.upload_settlement import UNCONFIRMED

    from app.models.experiment import Experiment
    from app.models.user import User

    query = (
        select(UploadCheckpoint, Experiment.name, User.name, User.email)
        .outerjoin(Experiment, Experiment.id == UploadCheckpoint.experiment_id)
        .outerjoin(User, User.id == UploadCheckpoint.user_id)
        .where(
            UploadCheckpoint.organization_id == int(current_user["org_id"]),
            UploadCheckpoint.purpose == "file",
            or_(
                UploadCheckpoint.stage.in_(("received", "cancelling")),
                and_(UploadCheckpoint.stage == "issued", UploadCheckpoint.last_error_category.in_(UNCONFIRMED)),
            ),
        )
        .order_by(UploadCheckpoint.created_at)
    )
    if not await _can_manage_all_uploads(session, current_user):
        query = query.where(UploadCheckpoint.user_id == int(current_user["sub"]))
    rows = (await session.execute(query)).all()
    return PendingUploadsResponse(
        uploads=[
            PendingUpload(
                upload_id=cp.id,
                filename=cp.filename,
                reference=cp.reference,
                stage=cp.stage,
                state=_attention_state(cp),
                reason=_stop_reason(cp),
                attempts=cp.attempts,
                next_attempt_at=cp.next_attempt_at,
                experiment_id=cp.experiment_id,
                experiment_name=exp_name,
                uploaded_by=user_name or user_email,
                uploaded_by_user_id=cp.user_id,
                created_at=cp.created_at,
            )
            for cp, exp_name, user_name, user_email in rows
        ]
    )


class UploadOutcome(BaseModel):
    """Where one upload ended up. Leaving the pending list does not say which: it may have been
    filed, cancelled or have failed, so an uploader following it asks for this."""

    upload_id: str
    reference: str | None = None
    stage: str
    outcome: Literal["pending", "filed", "cancelled", "failed"]
    # The pending list's state while the outcome is pending.
    state: str | None = None
    file_id: int | None = None


_OUTCOMES = {"filed": "filed", "cancelled": "cancelled", "failed": "failed"}


@router.get("/uploads/{upload_id}", response_model=UploadOutcome)
async def upload_outcome(
    upload_id: str,
    current_user: dict = require_permission("files", "upload"),
    session: AsyncSession = Depends(get_session),
):
    """One upload's outcome: filed (with its file), cancelled, failed, or still pending."""
    cp = await _pending_checkpoint(session, current_user, upload_id)
    outcome = _OUTCOMES.get(cp.stage, "pending")
    return UploadOutcome(
        upload_id=cp.id,
        reference=cp.reference,
        stage=cp.stage,
        outcome=outcome,
        state=_attention_state(cp) if outcome == "pending" else None,
        file_id=cp.file_id,
    )


def _stop_reason(cp: UploadCheckpoint) -> str | None:
    from app.services.upload_settlement import needs_release, stop_reason

    if cp.stage == "cancelling" and not needs_release(cp):
        return stop_reason(cp)
    return None


class CancelUploadRequest(BaseModel):
    # "interrupted" when the browser stops an upload after a failure rather than at the uploader's request.
    reason: Literal["cancelled", "interrupted"] = "cancelled"


def _attention_state(cp: UploadCheckpoint) -> str:
    from app.services.upload_settlement import needs_release

    if cp.stage in ("issued", "cancelling"):
        return "unconfirmed" if needs_release(cp) else "stopping"
    return "retrying" if cp.next_attempt_at is not None else "needs_attention"


@router.post("/uploads/{upload_id}/retry", response_model=FileResponse, responses=_RECEIVED_NOT_FILED)
async def retry_pending_upload(
    upload_id: str,
    current_user: dict = require_permission("files", "upload"),
    session: AsyncSession = Depends(get_session),
):
    """Retry filing a received upload now. 202 with the reference if it still cannot be filed."""
    org_id = int(current_user["org_id"])
    await _pending_checkpoint(session, current_user, upload_id)
    try:
        file = await UploadService.retry_upload(session, org_id, upload_id)
    except DomainError:
        raise
    except Exception as exc:
        raise upload_failure("retry", exc) from None
    await session.commit()
    await _remove_superseded_copy(file.id, org_id)
    file = await FileService.get_file(session, file.id, org_id)
    return (await _file_responses(session, [file], provenance=False))[0]


@router.post("/uploads/{upload_id}/cancel", status_code=204)
async def cancel_pending_upload(
    upload_id: str,
    body: CancelUploadRequest | None = None,
    current_user: dict = require_permission("files", "upload"),
    session: AsyncSession = Depends(get_session),
):
    """Cancel an unfiled upload. A received copy is deleted at once; an upload still being sent is
    cancelled with the provider, and keeps counting as a writer until the provider confirms it."""
    from app import database
    from app.services.upload_settlement import settle

    await _pending_checkpoint(session, current_user, upload_id)
    stage = await UploadService.cancel_upload(
        session,
        int(current_user["org_id"]),
        upload_id,
        int(current_user["sub"]),
        reason=(body or CancelUploadRequest()).reason,
    )
    await session.commit()
    if stage == "cancelling":
        # Ask the provider now; background reconciliation keeps trying if it does not confirm.
        try:
            await settle(database.async_session_factory, upload_id)
        except Exception as exc:  # settled later by the background job
            import logging

            logging.getLogger("bioaf.files").warning(
                "Upload cancellation not confirmed yet (error_type=%s)", type(exc).__name__
            )
    return Response(status_code=204)


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
    from app.platform.platform_config_service import PlatformConfigService

    config = await PlatformConfigService.get_many(session, ["ingest_bucket_name", "raw_bucket_name"])
    ingest_bucket = config.get("ingest_bucket_name", "")
    raw_bucket = config.get("raw_bucket_name", "")

    if not ingest_bucket or not raw_bucket:
        raise HTTPException(400, "Ingest or raw bucket not configured")

    from app.adapters.registry import get_storage_adapter

    adapter = get_storage_adapter()

    # Find stuck files: have experiment_id, URI still in ingest bucket
    stuck = (
        await session.execute(
            text(
                "SELECT id, experiment_id, file_type FROM files "
                "WHERE organization_id = :org_id "
                "AND experiment_id IS NOT NULL "
                "AND storage_uri LIKE :pattern"
            ).bindparams(org_id=org_id, pattern=adapter.build_uri(ingest_bucket, "") + "%")
        )
    ).fetchall()

    reconciled = 0
    failed = 0
    experiments_with_fastq: set[int] = set()

    for file_id, experiment_id, file_type in stuck:
        try:
            await FileOrganizationService.assign_file_to_experiment(
                session, file_id, experiment_id, user_id, org_id=org_id
            )
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
            ).bindparams(org_id=org_id, pattern=adapter.build_uri(raw_bucket, "") + "%")
        )
    ).scalar_one()

    return {
        "reconciled": reconciled,
        "failed": failed,
        "skipped": already_ok,
    }


@router.get("/stats")
async def file_stats(
    current_user: dict = require_permission("files", "view"),
    session: AsyncSession = Depends(get_session),
):
    """Return file counts grouped by source (artifacts vs uploaded) and file type."""
    from sqlalchemy import case, func, select

    from app.models.file import File

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
    for source, ftype, cnt in cast("Sequence[tuple[str, str, int]]", rows):
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
    current_user: dict = require_permission("files", "view"),
    file_type: str | None = None,
    experiment_id: int | None = None,
    project_id: int | None = None,
    source_type: str | None = None,
    sample_id: int | None = None,
    search: str | None = None,
    page: PageNumber = 1,
    page_size: UnboundedPageSize = 25,
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])

    files, total = await FileService.list_files(
        session, org_id, file_type, experiment_id, project_id, source_type, sample_id, search, page, page_size
    )
    return FileListResponse(
        files=await _file_responses(session, files),
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
    current_user: dict = require_permission("files", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])

    file = await FileService.get_file(session, file_id, org_id)
    if not file:
        raise HTTPException(404, "File not found")
    return (await _file_responses(session, [file]))[0]


@router.post("/{file_id}/verification/retry", response_model=FileResponse)
async def retry_file_verification(
    file_id: int,
    current_user: dict = require_permission("files", "edit"),
    session: AsyncSession = Depends(get_session),
):
    """Requeue a failed or mismatched checksum verification after its cause was corrected."""
    from app.services.file_verification_service import FileVerificationService

    org_id = int(current_user["org_id"])
    file = await FileService.get_file(session, file_id, org_id)
    if not file:
        raise HTTPException(404, "File not found")
    record = await FileVerificationService.retry(session, file_id, org_id)
    if record is None:
        raise HTTPException(404, "This file has no verification to retry")
    await session.commit()
    file = await FileService.get_file(session, file_id, org_id)
    return (await _file_responses(session, [file]))[0]


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

        url = await get_storage_adapter().generate_signed_url(file.storage_uri, method="GET", expiry_seconds=3600)
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

    Accepts either a Bearer credential (authorized here) or a short-lived
    content token in the query string (authorized when it was issued). Content
    tokens carry org_id but no user identity.
    """
    file = await file_for_content(session, request, file_id)
    if file.storage_deleted:
        raise HTTPException(410, "File storage has been deleted")

    try:
        from app.adapters.registry import get_storage_adapter

        data = await get_storage_adapter().read_bytes(file.storage_uri)

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
    # Every target is checked before anything changes: a refused link moves and writes nothing.
    await entity_references.verify_association_targets(
        session,
        org_id,
        project_id=body.project_id,
        experiment_id=body.experiment_id,
        sample_ids=[body.sample_id] if body.sample_id else [],
    )

    if body.project_id is not None:
        file.project_id = body.project_id

    if body.experiment_id is not None:
        if file.experiment_id != body.experiment_id:
            from app.services.file_organization import FileOrganizationService

            await FileOrganizationService.assign_file_to_experiment(
                session, file_id, body.experiment_id, user_id, org_id=org_id, commit=False
            )

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
    await _remove_superseded_copy(file_id, org_id)
    return {"status": "linked"}
