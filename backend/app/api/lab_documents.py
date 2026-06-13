"""Lab Documents API (ADR-059, ADR-060, ADR-061).

Endpoints under /api/lab-knowledge. Upload uses the signed-URL flow: the client
asks for an upload URL, PUTs bytes to GCS, then calls POST .../documents with the
returned token, at which point the server reads the GCS checksum, moves the object
into its versioned path, and creates the record.
"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import async_session_factory, get_session
from app.models.lab_document import LabDocumentVersion
from app.schemas.experiment import UserSummary
from app.schemas.lab_document import (
    LabDocumentCreate,
    LabDocumentListResponse,
    LabDocumentNoteCreate,
    LabDocumentNoteResponse,
    LabDocumentResponse,
    LabDocumentTagCreate,
    LabDocumentTagResponse,
    LabDocumentUpdate,
    LabDocumentUploadUrlRequest,
    LabDocumentUploadUrlResponse,
    LabDocumentUrlImportRequest,
    LabDocumentUrlImportResponse,
    LabDocumentVersionCreate,
    LabDocumentVersionResponse,
)
from app.services import role_service
from app.services.lab_document_service import (
    LabDocumentNoteService,
    LabDocumentService,
    LabDocumentTagService,
    NoteNotFoundError,
    NotePermissionError,
    TagInUseError,
)
from app.services.lab_document_upload_service import (
    LabDocumentUploadService,
    _assert_public_url,
)

router = APIRouter(prefix="/api/lab-knowledge", tags=["lab-knowledge"])


def _user_summary(user) -> UserSummary | None:
    if user is None:
        return None
    return UserSummary(id=user.id, name=user.name, email=user.email)


def _tag_responses(doc) -> list[LabDocumentTagResponse]:
    return [
        LabDocumentTagResponse(id=a.tag.id, name=a.tag.name)
        for a in sorted(doc.tag_assignments, key=lambda a: a.tag.name)
    ]


def _doc_response(doc) -> LabDocumentResponse:
    return LabDocumentResponse(
        id=doc.id,
        title=doc.title,
        description=doc.description,
        file_name=doc.file_name,
        current_version=doc.current_version,
        file_size_bytes=doc.file_size_bytes,
        mime_type=doc.mime_type,
        md5_checksum=doc.md5_checksum,
        is_archived=doc.is_archived,
        tags=_tag_responses(doc),
        created_by=_user_summary(doc.created_by),
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


# --- Documents ---------------------------------------------------------------


@router.get("/documents", response_model=LabDocumentListResponse)
async def list_documents(
    tag_ids: list[int] | None = Query(default=None),
    q: str | None = Query(default=None),
    include_archived: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    current_user: dict = require_permission("lab_documents", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    docs, total = await LabDocumentService.list_documents(
        session,
        org_id=org_id,
        tag_ids=tag_ids,
        query=q,
        include_archived=include_archived,
        page=page,
        page_size=page_size,
    )
    return LabDocumentListResponse(
        documents=[_doc_response(d) for d in docs], total=total, page=page, page_size=page_size
    )


@router.post("/documents/upload-url", response_model=LabDocumentUploadUrlResponse)
async def create_upload_url(
    body: LabDocumentUploadUrlRequest,
    request: Request,
    current_user: dict = require_permission("lab_documents", "manage"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    result = await LabDocumentUploadService.initiate(
        session,
        org_id,
        file_name=body.file_name,
        mime_type=body.mime_type,
        size_bytes=body.size_bytes,
        origin=request.headers.get("origin"),
    )
    return LabDocumentUploadUrlResponse(**result)


async def _finalize_document_from_token(
    session: AsyncSession,
    *,
    org_id: int,
    user_id: int,
    upload_token: str,
    title: str,
    description: str | None,
    tag_ids: list[int],
):
    """Shared finalize path for both the browser-upload and URL-import flows:
    read the stored object's checksum/size, create the v1 record, then move the
    object into its versioned path and point the record at it."""
    from app.adapters.registry import get_storage_adapter

    meta = await LabDocumentUploadService.read_metadata(session, upload_token=upload_token, org_id=org_id)
    doc = await LabDocumentService.create_document(
        session,
        org_id=org_id,
        user_id=user_id,
        title=title or meta["file_name"],
        description=description,
        file_name=meta["file_name"],
        # "pending" is a sentinel bucket; place() repoints the record at the real
        # versioned URI immediately below, before any storage op.
        gcs_uri=get_storage_adapter().build_uri("pending", upload_token),
        file_size_bytes=meta["size_bytes"],
        mime_type=meta["mime_type"],
        md5_checksum=meta["md5"],
        tag_ids=tag_ids,
    )
    dest_uri = await LabDocumentUploadService.place(
        session, upload_token=upload_token, org_id=org_id, document_id=doc.id, version=1
    )
    doc.gcs_uri = dest_uri
    await session.execute(
        update(LabDocumentVersion)
        .where(LabDocumentVersion.document_id == doc.id, LabDocumentVersion.version_number == 1)
        .values(gcs_uri=dest_uri)
    )
    await session.commit()
    return doc.id


@router.post("/documents", response_model=LabDocumentResponse)
async def create_document(
    body: LabDocumentCreate,
    current_user: dict = require_permission("lab_documents", "manage"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    doc_id = await _finalize_document_from_token(
        session,
        org_id=org_id,
        user_id=user_id,
        upload_token=body.upload_token,
        title=body.title,
        description=body.description,
        tag_ids=body.tag_ids,
    )
    doc = await LabDocumentService.get_document(session, document_id=doc_id, org_id=org_id)
    return _doc_response(doc)


def _url_import_response(row) -> LabDocumentUrlImportResponse:
    return LabDocumentUrlImportResponse(
        id=row.id, status=row.status, document_id=row.document_id, error_message=row.error_message
    )


@router.post("/documents/import-url", response_model=LabDocumentUrlImportResponse, status_code=202)
async def import_document_from_url(
    body: LabDocumentUrlImportRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = require_permission("lab_documents", "manage"),
    session: AsyncSession = Depends(get_session),
):
    """Add a document by having the server pull it from a public URL (matches the
    Reference Data URL-import option). The URL is validated and persisted here; the
    actual fetch runs as a background task that reads the URL back from the stored
    job (mirroring the Reference Data importer), so the user-supplied URL is never
    fetched directly in the request handler. Returns the import job to poll."""
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    # Validate up front (scheme + that the host is not an internal/metadata address)
    # so the caller gets immediate feedback. This performs no outbound request.
    _assert_public_url(body.url)

    row = await LabDocumentUploadService.create_url_import(
        session,
        org_id=org_id,
        user_id=user_id,
        url=body.url,
        title=body.title,
        description=body.description,
        tag_ids=body.tag_ids,
    )
    await session.commit()
    background_tasks.add_task(LabDocumentUploadService.run_url_import, async_session_factory, import_id=row.id)
    return _url_import_response(row)


@router.get("/documents/url-imports/{import_id}", response_model=LabDocumentUrlImportResponse)
async def get_url_import_status(
    import_id: int,
    current_user: dict = require_permission("lab_documents", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    row = await LabDocumentUploadService.get_url_import(session, org_id=org_id, import_id=import_id)
    if row is None:
        raise HTTPException(404, "Import not found")
    return _url_import_response(row)


@router.get("/documents/{document_id}", response_model=LabDocumentResponse)
async def get_document(
    document_id: int,
    current_user: dict = require_permission("lab_documents", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    doc = await LabDocumentService.get_document(session, document_id=document_id, org_id=org_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    return _doc_response(doc)


@router.patch("/documents/{document_id}", response_model=LabDocumentResponse)
async def update_document(
    document_id: int,
    body: LabDocumentUpdate,
    current_user: dict = require_permission("lab_documents", "manage"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    doc = await LabDocumentService.update_metadata(
        session,
        org_id=org_id,
        user_id=user_id,
        document_id=document_id,
        title=body.title,
        description=body.description,
        tag_ids=body.tag_ids,
    )
    if doc is None:
        raise HTTPException(404, "Document not found")
    await session.commit()
    doc = await LabDocumentService.get_document(session, document_id=document_id, org_id=org_id)
    return _doc_response(doc)


@router.get("/documents/{document_id}/versions", response_model=list[LabDocumentVersionResponse])
async def list_versions(
    document_id: int,
    current_user: dict = require_permission("lab_documents", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    doc = await LabDocumentService.get_document(session, document_id=document_id, org_id=org_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    return [
        LabDocumentVersionResponse(
            version_number=v.version_number,
            file_name=v.file_name,
            file_size_bytes=v.file_size_bytes,
            md5_checksum=v.md5_checksum,
            change_note=v.change_note,
            uploaded_by=_user_summary(v.uploaded_by),
            uploaded_at=v.uploaded_at,
        )
        for v in sorted(doc.versions, key=lambda v: v.version_number)
    ]


@router.post("/documents/{document_id}/versions", response_model=LabDocumentResponse)
async def upload_version(
    document_id: int,
    body: LabDocumentVersionCreate,
    current_user: dict = require_permission("lab_documents", "manage"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    doc = await LabDocumentService.get_document(session, document_id=document_id, org_id=org_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    if doc.is_archived:
        raise HTTPException(400, "Cannot upload a new version of an archived document")
    meta = await LabDocumentUploadService.read_metadata(session, upload_token=body.upload_token, org_id=org_id)
    dest_uri = await LabDocumentUploadService.place(
        session,
        upload_token=body.upload_token,
        org_id=org_id,
        document_id=doc.id,
        version=doc.current_version + 1,
    )
    await LabDocumentService.add_version(
        session,
        org_id=org_id,
        user_id=user_id,
        document_id=doc.id,
        gcs_uri=dest_uri,
        file_name=meta["file_name"],
        file_size_bytes=meta["size_bytes"],
        md5_checksum=meta["md5"],
        change_note=body.change_note,
    )
    await session.commit()
    doc = await LabDocumentService.get_document(session, document_id=doc.id, org_id=org_id)
    return _doc_response(doc)


@router.get("/documents/{document_id}/download")
async def download_document(
    document_id: int,
    version: int | None = Query(default=None),
    current_user: dict = require_permission("lab_documents", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    doc = await LabDocumentService.get_document(session, document_id=document_id, org_id=org_id)
    if doc is None:
        raise HTTPException(404, "Document not found")

    target = version or doc.current_version
    gcs_uri = next((v.storage_uri for v in doc.versions if v.version_number == target), None)
    if gcs_uri is None:
        raise HTTPException(404, "Version not found")

    try:
        from app.adapters.registry import get_storage_adapter

        url = await get_storage_adapter().generate_signed_url(gcs_uri, method="GET", expiry_seconds=3600)
    except Exception:
        raise HTTPException(502, "Could not generate download URL")

    from app.services.audit_service import log_action

    await log_action(
        session,
        user_id=user_id,
        entity_type="lab_document",
        entity_id=doc.id,
        action="downloaded",
        details={"version_number": target, "method": "signed_url"},
    )
    await session.commit()
    return {"download_url": url}


async def _download_document_bytes(session: AsyncSession, gcs_uri: str) -> bytes | None:
    """Fetch object bytes server-side (mirrors literature's PDF stream), so the
    inline viewer never has to read across the storage CORS boundary."""
    from app.adapters.registry import get_storage_adapter

    try:
        return await get_storage_adapter().read_bytes(gcs_uri)
    except Exception:
        return None


@router.get("/documents/{document_id}/content")
async def stream_document_content(
    document_id: int,
    version: int | None = Query(default=None),
    current_user: dict = require_permission("lab_documents", "view"),
    session: AsyncSession = Depends(get_session),
):
    """Stream the document bytes through the backend for inline viewing."""
    org_id = int(current_user["org_id"])
    doc = await LabDocumentService.get_document(session, document_id=document_id, org_id=org_id)
    if doc is None:
        raise HTTPException(404, "Document not found")

    target = version or doc.current_version
    chosen = next((v for v in doc.versions if v.version_number == target), None)
    if chosen is None:
        raise HTTPException(404, "Version not found")

    data = await _download_document_bytes(session, chosen.storage_uri)
    if data is None:
        raise HTTPException(502, "Could not fetch document")

    media_type = doc.mime_type or "application/octet-stream"

    def stream():
        yield data

    return StreamingResponse(
        stream(),
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{chosen.file_name}"'},
    )


def _note_response(note) -> LabDocumentNoteResponse:
    return LabDocumentNoteResponse(
        id=note.id,
        body="[deleted]" if note.deleted_at is not None else note.body,
        user=_user_summary(getattr(note, "user", None)),
        created_at=note.created_at,
        deleted=note.deleted_at is not None,
    )


@router.get("/documents/{document_id}/notes", response_model=list[LabDocumentNoteResponse])
async def list_notes(
    document_id: int,
    current_user: dict = require_permission("lab_documents", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    doc = await LabDocumentService.get_document(session, document_id=document_id, org_id=org_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    notes = await LabDocumentNoteService.list_notes(session, org_id=org_id, document_id=document_id)
    return [_note_response(n) for n in notes]


@router.post("/documents/{document_id}/notes", response_model=LabDocumentNoteResponse)
async def add_note(
    document_id: int,
    body: LabDocumentNoteCreate,
    current_user: dict = require_permission("lab_documents", "view"),
    session: AsyncSession = Depends(get_session),
):
    """Any user who can view documents can add a note (collaborative annotation)."""
    org_id, user_id = int(current_user["org_id"]), int(current_user["sub"])
    try:
        note = await LabDocumentNoteService.add_note(
            session, org_id=org_id, user_id=user_id, document_id=document_id, body=body.body
        )
    except NoteNotFoundError:
        raise HTTPException(404, "Document not found")
    await session.commit()
    return _note_response(note)


@router.delete("/documents/{document_id}/notes/{note_id}")
async def delete_note(
    document_id: int,
    note_id: int,
    current_user: dict = require_permission("lab_documents", "view"),
    session: AsyncSession = Depends(get_session),
):
    """Delete own note; deleting another user's note requires lab_documents:manage."""
    org_id, user_id = int(current_user["org_id"]), int(current_user["sub"])
    can_manage = await role_service.has_permission(session, int(current_user["role_id"]), "lab_documents", "manage")
    try:
        await LabDocumentNoteService.delete_note(
            session,
            org_id=org_id,
            user_id=user_id,
            document_id=document_id,
            note_id=note_id,
            can_manage=can_manage,
        )
    except NoteNotFoundError:
        raise HTTPException(404, "Note not found")
    except NotePermissionError as e:
        raise HTTPException(403, str(e))
    await session.commit()
    return {"status": "deleted"}


@router.post("/documents/{document_id}/archive", response_model=LabDocumentResponse)
async def archive_document(
    document_id: int,
    archived: bool = Query(default=True),
    current_user: dict = require_permission("lab_documents", "manage"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    doc = await LabDocumentService.set_archived(
        session, org_id=org_id, user_id=user_id, document_id=document_id, archived=archived
    )
    if doc is None:
        raise HTTPException(404, "Document not found")
    await session.commit()
    doc = await LabDocumentService.get_document(session, document_id=document_id, org_id=org_id)
    return _doc_response(doc)


# --- Tags --------------------------------------------------------------------


@router.get("/document-tags", response_model=list[LabDocumentTagResponse])
async def list_tags(
    current_user: dict = require_permission("lab_documents", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    tags = await LabDocumentTagService.list_tags(session, org_id=org_id)
    return [LabDocumentTagResponse(id=t.id, name=t.name) for t in tags]


@router.post("/document-tags", response_model=LabDocumentTagResponse)
async def create_tag(
    body: LabDocumentTagCreate,
    current_user: dict = require_permission("lab_document_tags", "manage"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    tag = await LabDocumentTagService.create_tag(session, org_id=org_id, user_id=user_id, name=body.name)
    await session.commit()
    return LabDocumentTagResponse(id=tag.id, name=tag.name)


@router.delete("/document-tags/{tag_id}")
async def delete_tag(
    tag_id: int,
    current_user: dict = require_permission("lab_document_tags", "manage"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    try:
        deleted = await LabDocumentTagService.delete_tag(session, org_id=org_id, user_id=user_id, tag_id=tag_id)
    except TagInUseError as e:
        raise HTTPException(409, {"error": "tag_in_use", "documents": e.document_titles})
    if not deleted:
        raise HTTPException(404, "Tag not found")
    await session.commit()
    return {"status": "deleted"}
