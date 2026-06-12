"""PDF thumbnail generation and GCS storage.

Renders page 1 of a PDF to a PNG thumbnail and uploads it to a
dedicated _thumbnails/ prefix in the results bucket so the scanner
does not re-index it.

All CPU-heavy rendering is offloaded to a thread pool to avoid
blocking the async event loop.
"""

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.platform_config_service import PlatformConfigService

logger = logging.getLogger("bioaf.thumbnail_service")

THUMBNAIL_PREFIX = "_thumbnails/"
THUMBNAIL_MAX_DIM = 1280
THUMBNAIL_DPI = 150


class ThumbnailService:
    @staticmethod
    def render_pdf_thumbnail(pdf_bytes: bytes) -> bytes | None:
        """Render page 1 of a PDF to a PNG image, fit within THUMBNAIL_MAX_DIM.

        Returns PNG bytes, or None if rendering fails.
        """
        try:
            import fitz

            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            if doc.page_count == 0:
                doc.close()
                return None

            page = doc[0]
            # Scale so the longest edge fits THUMBNAIL_MAX_DIM
            rect = page.rect
            scale = min(THUMBNAIL_MAX_DIM / rect.width, THUMBNAIL_MAX_DIM / rect.height, THUMBNAIL_DPI / 72.0)
            mat = fitz.Matrix(scale, scale)
            pixmap = page.get_pixmap(matrix=mat, alpha=False)
            png_bytes = pixmap.tobytes("png")
            doc.close()
            return png_bytes
        except Exception as e:
            logger.warning("Failed to render PDF thumbnail: %s", e)
            return None

    @staticmethod
    async def generate_and_upload(
        session: AsyncSession,
        source_gcs_uri: str,
        plot_entry_id: int,
    ) -> str | None:
        """Download a PDF from storage, render thumbnail, upload to _thumbnails/.

        Object I/O goes through the storage adapter (which offloads blocking SDK
        calls); the CPU-heavy PDF render is offloaded to a thread so the async
        event loop stays responsive. Returns the thumbnail URI, or None.
        """
        from app.adapters.registry import get_storage_adapter

        try:
            adapter = get_storage_adapter()
            pdf_bytes = await adapter.read_bytes(source_gcs_uri)

            png_bytes = await asyncio.to_thread(ThumbnailService.render_pdf_thumbnail, pdf_bytes)
            if not png_bytes:
                return None

            bucket_name, _ = adapter.parse_uri(source_gcs_uri)
            thumb_uri = adapter.build_uri(bucket_name, f"{THUMBNAIL_PREFIX}plot_{plot_entry_id}.png")
            await adapter.write_bytes(thumb_uri, png_bytes, content_type="image/png")

            logger.info("Generated thumbnail for plot %d: %s", plot_entry_id, thumb_uri)
            return thumb_uri
        except Exception as e:
            logger.warning("Failed to generate thumbnail for plot %d: %s", plot_entry_id, e)
            return None

    @staticmethod
    async def delete_thumbnail(session: AsyncSession, thumbnail_gcs_uri: str) -> bool:
        """Delete a thumbnail object from storage."""
        from app.adapters.registry import get_storage_adapter

        try:
            await get_storage_adapter().delete(thumbnail_gcs_uri)
            logger.info("Deleted thumbnail: %s", thumbnail_gcs_uri)
            return True
        except Exception as e:
            logger.warning("Failed to delete thumbnail %s: %s", thumbnail_gcs_uri, e)
            return False

    @staticmethod
    async def get_results_bucket(session: AsyncSession) -> str | None:
        """Read results_bucket_name from platform_config."""
        val = await PlatformConfigService.get(session, "results_bucket_name")
        if not val or val == "null":
            return None
        return val
