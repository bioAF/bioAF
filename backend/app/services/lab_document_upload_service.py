"""GCS mechanics for Lab Document uploads (ADR-061).

Signed-URL direct-to-GCS upload, then the server reads GCS's own md5Hash/size
on finalize and moves the object into the versioned path
``lab-knowledge/documents/{document_id}/v{n}/{file_name}``. Kept separate from
LabDocumentService so the DB logic stays testable and the GCS calls here are the
patch points in tests (mirroring how GcsStorageService is patched elsewhere).
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.gcs_storage import GcsStorageService
from app.services.upload_service import UploadService

PREFIX = "lab-knowledge/documents"

# token -> pending upload metadata. In-memory, consistent with UploadService;
# a process restart abandons in-flight tokens (the GCS object is simply unused).
_pending: dict[str, dict] = {}


class LabDocumentUploadService:
    @staticmethod
    async def _get_working_bucket(session: AsyncSession) -> str:
        result = await session.execute(text("SELECT value FROM platform_config WHERE key = 'working_bucket_name'"))
        name = result.scalar_one_or_none()
        if not name or name == "null":
            raise ValueError("Working bucket not configured. Deploy storage infrastructure first.")
        return name

    @staticmethod
    async def initiate(session: AsyncSession, org_id: int, *, file_name: str, mime_type: str | None = None) -> dict:
        token = str(uuid.uuid4())
        bucket = await LabDocumentUploadService._get_working_bucket(session)
        gcs_path = f"{PREFIX}/uploads/{token}/{file_name}"
        gcs_uri = f"gs://{bucket}/{gcs_path}"
        credentials = await UploadService._get_gcs_credentials(session)
        signed_url = await UploadService._generate_signed_upload_url(bucket, gcs_path, credentials=credentials)
        _pending[token] = {
            "org_id": org_id,
            "file_name": file_name,
            "mime_type": mime_type,
            "bucket": bucket,
            "gcs_path": gcs_path,
            "gcs_uri": gcs_uri,
        }
        return {"upload_token": token, "signed_url": signed_url, "gcs_uri": gcs_uri}

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
