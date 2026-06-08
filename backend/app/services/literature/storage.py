"""Literature GCS helpers: bucket lookup + path conventions (ADR-056)."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.platform_config_service import PlatformConfigService

logger = logging.getLogger("bioaf.literature.storage")


async def get_literature_bucket(session: AsyncSession) -> str | None:
    """Return the org's Literature bucket name from platform_config, or None
    if the storage stack has not been deployed (or this is a dev install)."""
    val = await PlatformConfigService.get(session, "literature_bucket_name")
    if not val or val == "null":
        return None
    return val


def paper_blob_prefix(paper_id: int) -> str:
    """GCS object prefix holding every file for a paper (PDF, extracted text,
    page images, thumbnail). Deleting the prefix frees all of a paper's
    storage."""
    return f"papers/{paper_id}/"


def pdf_blob_path(paper_id: int) -> str:
    return f"papers/{paper_id}/original.pdf"


def extracted_text_blob_path(paper_id: int) -> str:
    return f"papers/{paper_id}/extracted.txt"


def thumbnail_blob_path(paper_id: int) -> str:
    return f"papers/{paper_id}/thumbnail.png"


def page_image_blob_path(paper_id: int, page_n: int) -> str:
    return f"papers/{paper_id}/pages/{page_n}.png"


def gcs_uri(bucket: str, path: str) -> str:
    return f"gs://{bucket}/{path}"
