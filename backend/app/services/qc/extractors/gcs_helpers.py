"""Shared storage helpers for QC template extractors."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import get_app_logger
from app.platform.run_output_layout import directory_prefix

logger = get_app_logger(__name__)


async def run_output_directory(session: AsyncSession | None, run, adapter, output_uri: str | None) -> str | None:
    """The run's output directory URI with one trailing separator, for listing and reading under it.

    ``output_uri`` is the run's authorized location when the caller already resolved it (the QC
    dashboard does, once per run); otherwise it is resolved here. The location is the one the run
    wrote to (``RunOutputLocationService``), never one rebuilt from a bucket and experiment id.
    None, logged, when the location is refused: there is nothing this run may be read from.
    """
    if output_uri is None:
        from app.services.run_output_location_service import OutputLocationRefused, RunOutputLocationService

        try:
            output_uri = await RunOutputLocationService.authorized_uri(session, run, adapter)
        except OutputLocationRefused as e:
            logger.warning("QC for run %s has no output location to read: %s", run.id, e)
            return None
    return directory_prefix(output_uri)


__all__ = ["run_output_directory"]
