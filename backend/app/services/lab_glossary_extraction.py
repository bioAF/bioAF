"""Text extraction for glossary document scans (ADR-062).

Reuses the codebase's existing extraction approach (pdfplumber, as in
``document_service._extract_text_background``) and the GCS credential/parse
helpers on ``GcsStorageService`` rather than introducing a new extractor.
Isolated here so the scan service can lazily import it and tests can inject a
content provider instead.
"""

from __future__ import annotations

import io
import logging

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("bioaf.lab_glossary_extraction")


async def extract_text_from_gcs(session: AsyncSession, gcs_uri: str) -> str:
    """Download a lab document version from GCS and return its text. PDFs go
    through pdfplumber; everything else is decoded as UTF-8 best-effort."""
    from google.cloud import storage as gcs_storage

    from app.services.gcs_storage import GcsStorageService

    bucket_name, object_path = GcsStorageService._parse_gcs_uri(gcs_uri)
    credentials = await GcsStorageService.get_credentials(session)
    client = gcs_storage.Client(credentials=credentials)
    blob = client.bucket(bucket_name).blob(object_path)
    content = blob.download_as_bytes()

    if object_path.lower().endswith(".pdf"):
        return _extract_pdf_text(content)
    try:
        return content.decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001 - best-effort decode
        logger.warning("could not decode %s as text", gcs_uri)
        return ""


def _extract_pdf_text(content: bytes) -> str:
    try:
        import pdfplumber
    except ImportError:
        logger.warning("pdfplumber not installed; cannot extract PDF text for glossary scan")
        return ""
    out = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                out.append(page_text)
    return "\n".join(out)
