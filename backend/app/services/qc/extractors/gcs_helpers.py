"""Shared GCS helpers for QC template extractors."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def get_results_bucket(session: AsyncSession) -> str | None:
    """Read results bucket name from platform_config.

    Checks results_bucket_name first, then falls back to deriving it from
    raw_bucket_name (bioaf-raw-X -> bioaf-results-X) since the raw bucket
    is populated by Terraform before results_bucket_name.
    """
    result = await session.execute(
        text("SELECT key, value FROM platform_config WHERE key IN ('results_bucket_name', 'raw_bucket_name')")
    )
    config = {r[0]: r[1] for r in result.fetchall()}

    results = config.get("results_bucket_name")
    if results and results != "null":
        return results

    raw = config.get("raw_bucket_name", "")
    if raw and raw.startswith("bioaf-raw-"):
        return raw.replace("bioaf-raw-", "bioaf-results-", 1)

    return None


__all__ = ["get_results_bucket"]
