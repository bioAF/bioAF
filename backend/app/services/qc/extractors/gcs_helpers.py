"""Shared GCS helpers for QC template extractors."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.platform_config_service import PlatformConfigService
from app.platform.run_output_layout import results_bucket_from_config


async def get_results_bucket(session: AsyncSession) -> str | None:
    """Read results bucket name from platform_config.

    Checks results_bucket_name first, then falls back to deriving it from
    raw_bucket_name (bioaf-raw-X -> bioaf-results-X) since the raw bucket
    is populated by Terraform before results_bucket_name.
    """
    config = await PlatformConfigService.get_many(session, ["results_bucket_name", "raw_bucket_name"])
    return results_bucket_from_config(config)


__all__ = ["get_results_bucket"]
