"""PDF upload + async metadata extraction.

The endpoint path: accept the PDF, run the cheap synchronous part (DOI +
title from PyMuPDF metadata) so the user lands on a pre-filled form, then
queue the full-text extraction as an asyncio background task that updates
the literature_papers row when it completes.

GCS writes happen synchronously in the upload path because v1 does not have
chunked upload for Papers. The PDF is held in memory; PyMuPDF reads it from
a bytes buffer.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.database as _database
from app.models.literature import (
    EXTRACTION_COMPLETE,
    EXTRACTION_FAILED,
    EXTRACTION_PENDING,
    LiteraturePaper,
)
from app.services import audit_service
from app.services.literature import extraction, storage

logger = logging.getLogger("bioaf.literature.upload_service")


async def upload_pdf_to_gcs(
    session: AsyncSession,
    *,
    paper_id: int,
    pdf_bytes: bytes,
) -> str | None:
    """Upload the PDF bytes to gs://{literature_bucket}/papers/{paper_id}/original.pdf.

    Returns the gs:// URI on success. Returns None when the Literature bucket
    is not provisioned yet (e.g., dev install without storage stack); the
    caller leaves gcs_pdf_uri NULL but proceeds with extraction-from-bytes."""
    bucket = await storage.get_literature_bucket(session)
    if not bucket:
        return None
    path = storage.pdf_blob_path(paper_id)
    uri = storage.gcs_uri(bucket, path)

    from app.services.gcs_storage import GcsStorageService

    try:
        credentials = await GcsStorageService.get_credentials(session)
        from google.cloud import storage as gcs

        loop = asyncio.get_running_loop()

        def _upload() -> None:
            client = gcs.Client(credentials=credentials)
            bucket_obj = client.bucket(bucket)
            blob = bucket_obj.blob(path)
            blob.upload_from_string(pdf_bytes, content_type="application/pdf")

        await loop.run_in_executor(None, _upload)
        return uri
    except Exception as e:
        logger.warning("Failed to upload paper %s PDF to GCS: %s", paper_id, e)
        return None


def _delete_prefix(credentials, bucket: str, prefix: str) -> None:
    """Delete every blob under `prefix` in `bucket`. Runs in a thread
    executor; isolated as a seam so the deletion can be exercised in tests
    without the google.cloud.storage client."""
    from google.cloud import storage as gcs

    client = gcs.Client(credentials=credentials)
    bucket_obj = client.bucket(bucket)
    for blob in client.list_blobs(bucket_obj, prefix=prefix):
        blob.delete()


async def delete_paper_files(session: AsyncSession, *, paper_id: int) -> bool:
    """Best-effort deletion of every GCS object under papers/{paper_id}/.

    Returns True when a delete pass ran against a provisioned bucket, False
    when no Literature bucket is configured (dev install / storage stack not
    deployed). Never raises: a GCS failure is logged and the caller proceeds,
    so a paper is never left undeletable because storage is unavailable."""
    bucket = await storage.get_literature_bucket(session)
    if not bucket:
        return False
    prefix = storage.paper_blob_prefix(paper_id)
    from app.services.gcs_storage import GcsStorageService

    try:
        credentials = await GcsStorageService.get_credentials(session)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _delete_prefix, credentials, bucket, prefix)
        return True
    except Exception as e:
        logger.warning("Failed to delete GCS objects for paper %s: %s", paper_id, e)
        return False


async def upload_extracted_text_to_gcs(
    session: AsyncSession,
    *,
    paper_id: int,
    text: str,
) -> str | None:
    bucket = await storage.get_literature_bucket(session)
    if not bucket:
        return None
    path = storage.extracted_text_blob_path(paper_id)
    uri = storage.gcs_uri(bucket, path)
    from app.services.gcs_storage import GcsStorageService

    try:
        credentials = await GcsStorageService.get_credentials(session)
        from google.cloud import storage as gcs

        loop = asyncio.get_running_loop()

        def _upload() -> None:
            client = gcs.Client(credentials=credentials)
            bucket_obj = client.bucket(bucket)
            blob = bucket_obj.blob(path)
            blob.upload_from_string(text.encode("utf-8"), content_type="text/plain; charset=utf-8")

        await loop.run_in_executor(None, _upload)
        return uri
    except Exception as e:
        logger.warning("Failed to upload paper %s extracted text to GCS: %s", paper_id, e)
        return None


async def schedule_extraction(*, paper_id: int, pdf_bytes: bytes, user_id: int, api_key_id: int | None = None) -> None:
    """Spawn an asyncio task that re-extracts and persists in a fresh session."""
    asyncio.create_task(_extract_and_persist(paper_id, pdf_bytes, user_id, api_key_id))


async def _extract_and_persist(paper_id: int, pdf_bytes: bytes, user_id: int, api_key_id: int | None) -> None:
    factory = _database.async_session_factory
    if factory is None:
        return
    async with factory() as s:  # type: ignore[misc]
        try:
            result = extraction.extract_pdf_metadata(pdf_bytes)
        except Exception as e:  # pragma: no cover
            logger.exception("Extraction failed for paper %s", paper_id)
            await _mark_failed(s, paper_id, user_id, str(e), api_key_id)
            return

        rs = await s.execute(select(LiteraturePaper).where(LiteraturePaper.id == paper_id))
        paper = rs.scalar_one_or_none()
        if paper is None:
            return

        full_text = result.get("full_text")
        if full_text:
            uri = await upload_extracted_text_to_gcs(s, paper_id=paper_id, text=full_text)
            if uri:
                paper.extracted_text_uri = uri
                paper.has_full_text = True

        # Fill in any metadata fields that were not provided at upload time.
        if not paper.abstract and result.get("abstract"):
            paper.abstract = result["abstract"]
        if not paper.publication_date and result.get("publication_date"):
            paper.publication_date = result["publication_date"]
        if not paper.doi and result.get("doi"):
            paper.doi = result["doi"]

        paper.extraction_status = EXTRACTION_COMPLETE
        paper.extraction_error = None

        await audit_service.log_action(
            s,
            user_id=user_id,
            api_key_id=api_key_id,
            entity_type="literature_paper",
            entity_id=paper_id,
            action="update",
            details={"extraction_status": EXTRACTION_COMPLETE},
        )
        await s.commit()


async def _mark_failed(
    session: AsyncSession,
    paper_id: int,
    user_id: int,
    error: str,
    api_key_id: int | None,
) -> None:
    rs = await session.execute(select(LiteraturePaper).where(LiteraturePaper.id == paper_id))
    paper = rs.scalar_one_or_none()
    if paper is None:
        return
    paper.extraction_status = EXTRACTION_FAILED
    paper.extraction_error = error[:500]
    await audit_service.log_action(
        session,
        user_id=user_id,
        api_key_id=api_key_id,
        entity_type="literature_paper",
        entity_id=paper_id,
        action="update",
        details={"extraction_status": EXTRACTION_FAILED, "extraction_error": error[:200]},
    )
    await session.commit()


def synchronous_extract_basic(pdf_bytes: bytes) -> dict[str, Any]:
    """Return cheap fields (title, authors, doi, publication_date) for the
    pre-fill form during upload. Skips full-text extraction."""
    return extraction.extract_pdf_metadata(pdf_bytes)


async def mark_extraction_pending(
    session: AsyncSession,
    *,
    paper: LiteraturePaper,
    user_id: int,
    api_key_id: int | None = None,
) -> None:
    paper.extraction_status = EXTRACTION_PENDING
    paper.extraction_error = None
    await audit_service.log_action(
        session,
        user_id=user_id,
        api_key_id=api_key_id,
        entity_type="literature_paper",
        entity_id=paper.id,
        action="update",
        details={"extraction_status": EXTRACTION_PENDING},
    )
