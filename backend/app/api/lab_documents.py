"""Lab Documents API (ADR-059, ADR-060, ADR-061).

Endpoints under /api/lab-knowledge. Upload uses the signed-URL flow: the client
asks for an upload URL, PUTs bytes to GCS, then calls POST .../documents with the
returned token, at which point the server reads the GCS checksum, moves the object
into its versioned path, and creates the record.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.models.lab_document import LabDocumentVersion
from app.schemas.experiment import UserSummary
from app.schemas.lab_document import (
    LabDocumentCreate,
    LabDocumentListResponse,
    LabDocumentResponse,
    LabDocumentTagCreate,
    LabDocumentTagResponse,
    LabDocumentUpdate,
    LabDocumentUploadUrlRequest,
    LabDocumentUploadUrlResponse,
    LabDocumentVersionCreate,
    LabDocumentVersionResponse,
)
from app.services.lab_document_service import (
    LabDocumentService,
    LabDocumentTagService,
    TagInUseError,
)
from app.services.lab_document_upload_service import LabDocumentUploadService

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
    current_user: dict = require_permission("lab_documents", "manage"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    try:
        result = await LabDocumentUploadService.initiate(
            session, org_id, file_name=body.file_name, mime_type=body.mime_type
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return LabDocumentUploadUrlResponse(**result)


@router.post("/documents", response_model=LabDocumentResponse)
async def create_document(
    body: LabDocumentCreate,
    current_user: dict = require_permission("lab_documents", "manage"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    try:
        meta = await LabDocumentUploadService.read_metadata(session, upload_token=body.upload_token, org_id=org_id)
        doc = await LabDocumentService.create_document(
            session,
            org_id=org_id,
            user_id=user_id,
            title=body.title,
            description=body.description,
            file_name=meta["file_name"],
            gcs_uri=f"gs://pending/{body.upload_token}",
            file_size_bytes=meta["size_bytes"],
            mime_type=meta["mime_type"],
            md5_checksum=meta["md5"],
            tag_ids=body.tag_ids,
        )
        dest_uri = await LabDocumentUploadService.place(
            session, upload_token=body.upload_token, org_id=org_id, document_id=doc.id, version=1
        )
        doc.gcs_uri = dest_uri
        await session.execute(
            update(LabDocumentVersion)
            .where(LabDocumentVersion.document_id == doc.id, LabDocumentVersion.version_number == 1)
            .values(gcs_uri=dest_uri)
        )
        await session.commit()
    except ValueError as e:
        raise HTTPException(400, str(e))
    doc = await LabDocumentService.get_document(session, document_id=doc.id, org_id=org_id)
    return _doc_response(doc)


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
    try:
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
    except ValueError as e:
        raise HTTPException(400, str(e))
    doc = await LabDocumentService.get_document(session, document_id=doc.id, org_id=org_id)
    return _doc_response(doc)


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
