from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.schemas.document import (
    DocumentLinkRequest,
    DocumentResponse,
    DocumentSearchResponse,
    DocumentUpdate,
)
from app.schemas.file import FileResponse
from app.services.document_service import DocumentService

router = APIRouter(prefix="/api/documents", tags=["documents"])


def _doc_response(doc) -> DocumentResponse:
    file_resp = None
    if doc.file:
        file_resp = FileResponse(
            id=doc.file.id,
            filename=doc.file.filename,
            gcs_uri=doc.file.gcs_uri,
            size_bytes=doc.file.size_bytes,
            md5_checksum=doc.file.md5_checksum,
            file_type=doc.file.file_type,
            tags=doc.file.tags_json if isinstance(doc.file.tags_json, list) else [],
            uploader=None,
            upload_timestamp=doc.file.upload_timestamp,
            created_at=doc.file.created_at,
        )
    return DocumentResponse(
        id=doc.id,
        title=doc.title,
        file=file_resp,
        has_extracted_text=bool(doc.extracted_text),
        linked_experiment_id=doc.linked_experiment_id,
        linked_sample_id=doc.linked_sample_id,
        linked_pipeline_run_id=doc.linked_pipeline_run_id,
        created_at=doc.created_at,
    )


@router.post("/upload", response_model=DocumentResponse)
async def upload_document(
    file: UploadFile = File(...),
    title: str | None = None,
    experiment_id: int | None = None,
    sample_id: int | None = None,
    current_user: dict = require_permission("files", "upload"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    content = await file.read()
    doc = await DocumentService.upload_document(
        session,
        org_id,
        user_id,
        filename=file.filename or "document.pdf",
        content=content,
        title=title,
        experiment_id=experiment_id,
        sample_id=sample_id,
    )
    await session.commit()
    doc = await DocumentService.get_document(session, doc.id, org_id)
    return _doc_response(doc)


@router.get("", response_model=DocumentSearchResponse)
async def list_documents(
    request: Request,
    query: str | None = None,
    experiment_id: int | None = None,
    page: int = 1,
    page_size: int = 25,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    docs, total = await DocumentService.search_documents(
        session, org_id, query=query, experiment_id=experiment_id, page=page, page_size=page_size
    )
    return DocumentSearchResponse(
        documents=[_doc_response(d) for d in docs],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    doc = await DocumentService.get_document(session, document_id, org_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    return _doc_response(doc)


@router.get("/{document_id}/download")
async def download_document(
    document_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    doc = await DocumentService.get_document(session, document_id, org_id)
    if not doc:
        raise HTTPException(404, "Document not found")

    try:
        from google.cloud import storage as gcs_storage

        client = gcs_storage.Client()
        parts = doc.file.gcs_uri.replace("gs://", "").split("/", 1)
        bucket = client.bucket(parts[0])
        blob = bucket.blob(parts[1])
        url = blob.generate_signed_url(version="v4", expiration=3600, method="GET")
        return {"download_url": url}
    except Exception:
        return {"download_url": doc.file.gcs_uri if doc.file else ""}


@router.patch("/{document_id}", response_model=DocumentResponse)
async def update_document(
    document_id: int,
    body: DocumentUpdate,
    current_user: dict = require_permission("files", "edit"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    doc = await DocumentService.update_document(session, document_id, org_id, user_id, title=body.title)
    if not doc:
        raise HTTPException(404, "Document not found")
    await session.commit()
    doc = await DocumentService.get_document(session, document_id, org_id)
    return _doc_response(doc)


@router.delete("/{document_id}")
async def delete_document(
    document_id: int,
    current_user: dict = require_permission("files", "delete"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    deleted = await DocumentService.delete_document(session, document_id, org_id, user_id)
    if not deleted:
        raise HTTPException(404, "Document not found")
    await session.commit()
    return {"status": "deleted"}


@router.post("/{document_id}/link")
async def link_document(
    document_id: int,
    body: DocumentLinkRequest,
    current_user: dict = require_permission("files", "edit"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    doc = await DocumentService.link_document(
        session,
        document_id,
        org_id,
        user_id,
        experiment_id=body.experiment_id,
        sample_id=body.sample_id,
        pipeline_run_id=body.pipeline_run_id,
    )
    if not doc:
        raise HTTPException(404, "Document not found")
    await session.commit()
    return {"status": "linked"}
