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
    """Download a lab document version from storage and return its text. PDFs go
    through pdfplumber; everything else is decoded as UTF-8 best-effort."""
    from app.adapters.registry import get_storage_adapter

    content = await get_storage_adapter().read_bytes(gcs_uri)

    if gcs_uri.lower().endswith(".pdf"):
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
