"""GCS mechanics for Lab Document uploads (ADR-061).

Direct-to-GCS upload via a resumable upload session (created with the request
Origin so the browser's cross-origin PUT is accepted, mirroring the references
upload flow), then the server reads GCS's own md5Hash/size on finalize and moves
the object into the versioned path
``lab-knowledge/documents/{document_id}/v{n}/{file_name}``. A second entry point
(``create_url_import`` + the ``run_url_import`` background executor) lets the
server pull the bytes from a public URL instead, reading the URL back from a
persisted job so the user-supplied URL is not fetched in the request handler.
Kept separate from LabDocumentService so the DB logic stays testable and the GCS
calls here are the patch points in tests (mirroring GcsStorageService elsewhere).
"""

from __future__ import annotations

import ipaddress
import logging
import os
import socket
import uuid
from datetime import UTC, datetime
from urllib.parse import unquote, urljoin, urlparse

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("bioaf.lab_document_upload")

PREFIX = "lab-knowledge/documents"

# Cap server-side URL imports so a hostile or mistaken URL can't exhaust memory
# or disk. Lab documents are small (manuals, policies, PDFs), so 100 MiB is ample.
MAX_URL_DOWNLOAD_BYTES = 100 * 1024 * 1024
URL_FETCH_TIMEOUT_SECONDS = 30.0
# Bound the manual redirect chain (each hop is re-validated against the SSRF guard).
MAX_URL_REDIRECTS = 5


def _assert_public_url(url: str) -> None:
    """SSRF guard for server-side URL fetches.

    Rejects anything that is not http(s) or whose host resolves to a non-public
    address: loopback, private ranges, link-local (which includes the cloud
    instance metadata endpoint 169.254.169.254 / fd00:ec2::254), reserved,
    multicast, or unspecified. Raises ValueError on any violation. Called for the
    initial URL and again for every redirect target so an external host cannot
    bounce the fetch onto an internal address.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL must start with http:// or https://")
    host = parsed.hostname
    if not host:
        raise ValueError("URL has no host")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ValueError("Could not resolve URL host")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError("URL host is not allowed (resolves to a non-public address)")


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
    async def _create_resumable_session(
        bucket_name: str,
        blob_path: str,
        *,
        content_type: str,
        size_bytes: int | None,
        origin: str | None = None,
    ) -> str:
        """Create a resumable upload session and return its session URL.

        The browser PUTs bytes directly against this URL. Passing ``origin`` lets
        the backend accept the cross-origin upload without bucket-level CORS
        config (the fix for the "Failed to fetch" upload bug). Tests monkey-patch
        this to avoid real storage calls.
        """
        from app.adapters.registry import get_storage_adapter

        return await get_storage_adapter().create_resumable_upload_url(
            f"gs://{bucket_name}/{blob_path}",
            content_type=content_type,
            size_bytes=size_bytes,
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
        signed_url = await LabDocumentUploadService._create_resumable_session(
            bucket,
            gcs_path,
            content_type=mime_type or "application/octet-stream",
            size_bytes=size_bytes,
            origin=origin,
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
        non-http(s) scheme, a host that resolves to a non-public address (SSRF
        guard), a too-large body, or any transport/HTTP error. Redirects are
        followed manually so every hop is re-validated. The file name is derived
        from the URL path (or Content-Disposition). Kept as a patch point so tests
        need no network.
        """
        import httpx

        current = url
        try:
            # Redirects are disabled at the client; we follow them by hand so each
            # target passes the SSRF guard before we connect to it.
            async with httpx.AsyncClient(follow_redirects=False, timeout=URL_FETCH_TIMEOUT_SECONDS) as http:
                for _ in range(MAX_URL_REDIRECTS + 1):
                    _assert_public_url(current)
                    async with http.stream("GET", current) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise ValueError("Redirect without a location")
                            current = urljoin(current, location)
                            continue
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
                        file_name = _filename_from_response(
                            urlparse(current), response.headers.get("content-disposition")
                        )
                        if not content:
                            raise ValueError("The URL returned an empty file")
                        return content, file_name, mime_type
                raise ValueError("Too many redirects")
        except ValueError:
            raise
        except httpx.HTTPStatusError as exc:
            raise ValueError(f"Could not fetch URL (HTTP {exc.response.status_code})")
        except httpx.HTTPError as exc:
            raise ValueError(f"Could not fetch URL: {exc}")

    @staticmethod
    async def _upload_bytes(bucket_name: str, gcs_path: str, content: bytes, content_type: str | None) -> None:
        """Upload in-memory bytes to storage (server-side, no browser involved).
        Patch point in tests."""
        from app.adapters.registry import get_storage_adapter

        await get_storage_adapter().write_bytes(
            f"gs://{bucket_name}/{gcs_path}", content, content_type=content_type or "application/octet-stream"
        )

    # --- URL import job (fetch decoupled from the request) -------------------

    @staticmethod
    async def create_url_import(
        session: AsyncSession,
        *,
        org_id: int,
        user_id: int,
        url: str,
        title: str | None,
        description: str | None,
        tag_ids: list[int],
    ):
        """Persist a pending URL-import job. The actual fetch is run later by a
        background task that reads the URL back from this row, so the user-supplied
        URL never flows straight into an outbound request in the request handler
        (matching the Reference Data importer and the glossary scan job)."""
        from app.models.lab_document import LabDocumentUrlImport

        row = LabDocumentUrlImport(
            organization_id=org_id,
            initiated_by_user_id=user_id,
            url=url,
            title=title,
            description=description,
            tag_ids=tag_ids or None,
            status="pending",
        )
        session.add(row)
        await session.flush()
        return row

    @staticmethod
    async def get_url_import(session: AsyncSession, *, org_id: int, import_id: int):
        from app.models.lab_document import LabDocumentUrlImport

        return (
            await session.execute(
                select(LabDocumentUrlImport).where(
                    LabDocumentUrlImport.id == import_id,
                    LabDocumentUrlImport.organization_id == org_id,
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def run_url_import(session_factory, *, import_id: int, fetch_override=None) -> None:
        """Background executor for a URL import. Owns its DB session so it can run
        outside the request. Reads the URL from the persisted job row (not from the
        request), fetches it (SSRF-guarded), stores it, and creates the v1 document,
        recording success/failure on the job. ``fetch_override`` replaces the network
        fetch in tests."""
        from app.models.lab_document import LabDocumentUrlImport

        try:
            async with session_factory() as session:
                row = (
                    await session.execute(select(LabDocumentUrlImport).where(LabDocumentUrlImport.id == import_id))
                ).scalar_one()
                row.status = "running"
                org_id = row.organization_id
                user_id = row.initiated_by_user_id
                url = row.url  # read back from the DB, decoupled from the request
                title = row.title
                description = row.description
                tag_ids = list(row.tag_ids or [])
                await session.commit()

            async with session_factory() as session:
                doc_id = await LabDocumentUploadService._import_url_to_document(
                    session,
                    org_id=org_id,
                    user_id=user_id,
                    url=url,
                    title=title,
                    description=description,
                    tag_ids=tag_ids,
                    fetch_override=fetch_override,
                )
                await session.commit()

            async with session_factory() as session:
                row = (
                    await session.execute(select(LabDocumentUrlImport).where(LabDocumentUrlImport.id == import_id))
                ).scalar_one()
                row.status = "complete"
                row.document_id = doc_id
                row.completed_at = datetime.now(UTC)
                await session.commit()
        except Exception as exc:  # noqa: BLE001 - last-resort guard; nothing re-raised
            logger.exception("lab document URL import failed: import_id=%d", import_id)
            await LabDocumentUploadService._fail_url_import(session_factory, import_id, str(exc))

    @staticmethod
    async def _import_url_to_document(
        session: AsyncSession,
        *,
        org_id: int,
        user_id: int,
        url: str,
        title: str | None,
        description: str | None,
        tag_ids: list[int],
        fetch_override=None,
    ) -> int:
        """Fetch the URL (SSRF-guarded), store the bytes in GCS, and create the v1
        document. Returns the new document id."""
        from sqlalchemy import update as sa_update

        from app.models.lab_document import LabDocumentVersion
        from app.services.lab_document_service import LabDocumentService

        fetch = fetch_override or LabDocumentUploadService._fetch_url
        content, fetched_name, mime_type = await fetch(url)
        name = fetched_name or "document"
        token = str(uuid.uuid4())
        bucket = await LabDocumentUploadService._get_working_bucket(session)
        gcs_path = f"{PREFIX}/uploads/{token}/{name}"
        gcs_uri = f"gs://{bucket}/{gcs_path}"
        await LabDocumentUploadService._upload_bytes(bucket, gcs_path, content, mime_type)
        _pending[token] = {
            "org_id": org_id,
            "file_name": name,
            "mime_type": mime_type,
            "bucket": bucket,
            "gcs_path": gcs_path,
            "gcs_uri": gcs_uri,
        }
        meta = await LabDocumentUploadService.read_metadata(session, upload_token=token, org_id=org_id)
        doc = await LabDocumentService.create_document(
            session,
            org_id=org_id,
            user_id=user_id,
            title=title or meta["file_name"],
            description=description,
            file_name=meta["file_name"],
            gcs_uri=f"gs://pending/{token}",
            file_size_bytes=meta["size_bytes"],
            mime_type=meta["mime_type"],
            md5_checksum=meta["md5"],
            tag_ids=tag_ids,
        )
        dest_uri = await LabDocumentUploadService.place(
            session, upload_token=token, org_id=org_id, document_id=doc.id, version=1
        )
        doc.gcs_uri = dest_uri
        await session.execute(
            sa_update(LabDocumentVersion)
            .where(LabDocumentVersion.document_id == doc.id, LabDocumentVersion.version_number == 1)
            .values(gcs_uri=dest_uri)
        )
        return doc.id

    @staticmethod
    async def _fail_url_import(session_factory, import_id: int, error: str) -> None:
        from app.models.lab_document import LabDocumentUrlImport

        try:
            async with session_factory() as session:
                row = (
                    await session.execute(select(LabDocumentUrlImport).where(LabDocumentUrlImport.id == import_id))
                ).scalar_one_or_none()
                if row is None or row.status in ("complete", "failed"):
                    return
                row.status = "failed"
                row.error_message = error[:4000]
                row.completed_at = datetime.now(UTC)
                await session.commit()
        except Exception:  # noqa: BLE001 - never raise from the guard
            logger.exception("failed to mark URL import failed: import_id=%d", import_id)

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
        from app.adapters.models import StorageObjectNotFound
        from app.adapters.registry import get_storage_adapter

        try:
            metadata = await get_storage_adapter().get_object_metadata(pending["gcs_uri"])
        except StorageObjectNotFound:
            raise ValueError("Uploaded object not found in storage")
        return {
            "file_name": pending["file_name"],
            "mime_type": pending["mime_type"],
            "size_bytes": metadata.size_bytes,
            "md5": metadata.md5_hash,
        }

    @staticmethod
    async def place(session: AsyncSession, *, upload_token: str, org_id: int, document_id: int, version: int) -> str:
        """Move the uploaded object into its versioned path and return the dest URI."""
        pending = LabDocumentUploadService._peek(upload_token, org_id)
        dest_path = f"{PREFIX}/{document_id}/v{version}/{pending['file_name']}"
        dest_uri = f"gs://{pending['bucket']}/{dest_path}"
        from app.adapters.registry import get_storage_adapter

        await get_storage_adapter().move(pending["gcs_uri"], dest_uri)
        _pending.pop(upload_token, None)
        return dest_uri
