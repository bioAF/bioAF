"""GCS mechanics for Lab Document uploads (ADR-061).

Direct-to-GCS upload via a resumable upload session (created with the request
Origin so the browser's cross-origin PUT is accepted, mirroring the references
upload flow), then the server reads GCS's own md5Hash/size on finalize and moves
the object into the versioned path
``lab-knowledge/documents/{document_id}/v{n}/{file_name}``. A second entry point
(``initiate_from_url``) lets the server pull the bytes from a public URL instead.
Kept separate from LabDocumentService so the DB logic stays testable and the GCS
calls here are the patch points in tests (mirroring GcsStorageService elsewhere).
"""

from __future__ import annotations

import logging
import os
import uuid
from urllib.parse import unquote, urlparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.gcs_storage import GcsStorageService
from app.services.upload_service import UploadService

logger = logging.getLogger("bioaf.lab_document_upload")

PREFIX = "lab-knowledge/documents"

# Cap server-side URL imports so a hostile or mistaken URL can't exhaust memory
# or disk. Lab documents are small (manuals, policies, PDFs), so 100 MiB is ample.
MAX_URL_DOWNLOAD_BYTES = 100 * 1024 * 1024
URL_FETCH_TIMEOUT_SECONDS = 30.0

# token -> pending upload metadata. In-memory, consistent with UploadService;
# a process restart abandons in-flight tokens (the GCS object is simply unused).
_pending: dict[str, dict] = {}


def _filename_from_response(parsed_url, content_disposition: str | None) -> str:
    """Best-effort file name for a URL import: prefer Content-Disposition's
    filename, else the URL path basename, else a generic default."""
    if content_disposition:
        for part in content_disposition.split(";"):
            part = part.strip()
            if part.lower().startswith("filename="):
                value = part[len("filename=") :].strip().strip('"')
                if value:
                    return os.path.basename(unquote(value))
    base = os.path.basename(unquote(parsed_url.path or "")).strip()
    return base or "document"


class LabDocumentUploadService:
    @staticmethod
    async def _get_working_bucket(session: AsyncSession) -> str:
        result = await session.execute(text("SELECT value FROM platform_config WHERE key = 'working_bucket_name'"))
        name = result.scalar_one_or_none()
        if not name or name == "null":
            raise ValueError("Working bucket not configured. Deploy storage infrastructure first.")
        return name

    @staticmethod
    def _create_resumable_session(
        bucket_name: str,
        blob_path: str,
        *,
        content_type: str,
        size_bytes: int | None,
        origin: str | None = None,
        credentials=None,
    ) -> str:
        """Create a GCS resumable upload session and return its session URL.

        The browser PUTs bytes directly against this URL. Passing ``origin`` lets
        GCS accept the cross-origin upload without bucket-level CORS config (this
        is the fix for the "Failed to fetch" upload bug). Tests monkey-patch this
        to avoid real GCS calls.
        """
        from google.cloud import storage as gcs_storage

        client = gcs_storage.Client(credentials=credentials)
        blob = client.bucket(bucket_name).blob(blob_path)
        return blob.create_resumable_upload_session(
            content_type=content_type,
            size=size_bytes,
            origin=origin,
        )

    @staticmethod
    async def initiate(
        session: AsyncSession,
        org_id: int,
        *,
        file_name: str,
        mime_type: str | None = None,
        size_bytes: int | None = None,
        origin: str | None = None,
    ) -> dict:
        token = str(uuid.uuid4())
        bucket = await LabDocumentUploadService._get_working_bucket(session)
        gcs_path = f"{PREFIX}/uploads/{token}/{file_name}"
        gcs_uri = f"gs://{bucket}/{gcs_path}"
        credentials = await UploadService._get_gcs_credentials(session)
        signed_url = LabDocumentUploadService._create_resumable_session(
            bucket,
            gcs_path,
            content_type=mime_type or "application/octet-stream",
            size_bytes=size_bytes,
            origin=origin,
            credentials=credentials,
        )
        _pending[token] = {
            "org_id": org_id,
            "file_name": file_name,
            "mime_type": mime_type,
            "bucket": bucket,
            "gcs_path": gcs_path,
            "gcs_uri": gcs_uri,
        }
        return {"upload_token": token, "signed_url": signed_url, "gcs_uri": gcs_uri}

    # --- URL import (server-side fetch) --------------------------------------

    @staticmethod
    async def _fetch_url(url: str) -> tuple[bytes, str, str | None]:
        """Download a document from a public http(s) URL.

        Returns ``(content, file_name, mime_type)``. Raises ValueError for a
        non-http(s) scheme, a too-large body, or any transport/HTTP error. The
        file name is derived from the URL path (or Content-Disposition). Kept as
        a patch point so tests need no network.
        """
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("URL must start with http:// or https://")

        import httpx

        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=URL_FETCH_TIMEOUT_SECONDS
            ) as http:
                async with http.stream("GET", url) as response:
                    response.raise_for_status()
                    declared = response.headers.get("content-length")
                    if declared is not None and int(declared) > MAX_URL_DOWNLOAD_BYTES:
                        raise ValueError("File at URL exceeds the 100 MB import limit")
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > MAX_URL_DOWNLOAD_BYTES:
                            raise ValueError("File at URL exceeds the 100 MB import limit")
                        chunks.append(chunk)
                    content = b"".join(chunks)
                    mime_type = (response.headers.get("content-type") or "").split(";")[0].strip() or None
                    file_name = _filename_from_response(parsed, response.headers.get("content-disposition"))
        except ValueError:
            raise
        except httpx.HTTPStatusError as exc:
            raise ValueError(f"Could not fetch URL (HTTP {exc.response.status_code})")
        except httpx.HTTPError as exc:
            raise ValueError(f"Could not fetch URL: {exc}")
        if not content:
            raise ValueError("The URL returned an empty file")
        return content, file_name, mime_type

    @staticmethod
    async def _upload_bytes(
        bucket_name: str, gcs_path: str, content: bytes, content_type: str | None, credentials=None
    ) -> None:
        """Upload in-memory bytes to GCS (server-side, no browser involved). Patch
        point in tests."""
        import asyncio

        from google.cloud import storage as gcs_storage

        def _do_upload() -> None:
            client = gcs_storage.Client(credentials=credentials)
            blob = client.bucket(bucket_name).blob(gcs_path)
            blob.upload_from_string(content, content_type=content_type or "application/octet-stream")

        await asyncio.to_thread(_do_upload)

    @staticmethod
    async def initiate_from_url(
        session: AsyncSession, org_id: int, *, url: str, file_name: str | None = None
    ) -> dict:
        """Fetch a document from a URL into the uploads area and register a pending
        token, so the existing finalize path (read_metadata -> create -> place)
        can complete it exactly like a browser upload."""
        content, fetched_name, mime_type = await LabDocumentUploadService._fetch_url(url)
        name = file_name or fetched_name or "document"
        token = str(uuid.uuid4())
        bucket = await LabDocumentUploadService._get_working_bucket(session)
        gcs_path = f"{PREFIX}/uploads/{token}/{name}"
        gcs_uri = f"gs://{bucket}/{gcs_path}"
        credentials = await UploadService._get_gcs_credentials(session)
        await LabDocumentUploadService._upload_bytes(bucket, gcs_path, content, mime_type, credentials)
        _pending[token] = {
            "org_id": org_id,
            "file_name": name,
            "mime_type": mime_type,
            "bucket": bucket,
            "gcs_path": gcs_path,
            "gcs_uri": gcs_uri,
        }
        return {"upload_token": token, "file_name": name, "mime_type": mime_type, "gcs_uri": gcs_uri}

    @staticmethod
    def _peek(token: str, org_id: int) -> dict:
        pending = _pending.get(token)
        if not pending or pending["org_id"] != org_id:
            raise ValueError("Invalid or expired upload_token")
        return pending

    @staticmethod
    async def read_metadata(session: AsyncSession, *, upload_token: str, org_id: int) -> dict:
        """Read size + GCS-computed md5 of the uploaded object. Leaves the token
        pending so the caller can still place() it after creating the record."""
        pending = LabDocumentUploadService._peek(upload_token, org_id)
        credentials = await GcsStorageService.get_credentials(session)
        from google.cloud import storage

        client = storage.Client(credentials=credentials) if credentials else storage.Client()
        blob = client.bucket(pending["bucket"]).get_blob(pending["gcs_path"])
        if blob is None:
            raise ValueError("Uploaded object not found in storage")
        return {
            "file_name": pending["file_name"],
            "mime_type": pending["mime_type"],
            "size_bytes": blob.size,
            "md5": blob.md5_hash,
        }

    @staticmethod
    async def place(session: AsyncSession, *, upload_token: str, org_id: int, document_id: int, version: int) -> str:
        """Move the uploaded object into its versioned path and return the dest URI."""
        pending = LabDocumentUploadService._peek(upload_token, org_id)
        dest_path = f"{PREFIX}/{document_id}/v{version}/{pending['file_name']}"
        dest_uri = f"gs://{pending['bucket']}/{dest_path}"
        await GcsStorageService.move_file(pending["gcs_uri"], dest_uri)
        _pending.pop(upload_token, None)
        return dest_uri
