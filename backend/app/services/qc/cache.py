"""Writing QC metric caches to storage, admitted like any background write (storage lifecycle 11G)."""

from __future__ import annotations

import json
import logging

logger = logging.getLogger("bioaf.qc.cache")


async def write_metrics_cache(adapter, cache_uri: str, metrics: dict) -> None:
    """Cache extracted metrics next to the run. Skipped while storage writes are paused: the
    cache is an optimization, and the metrics are extracted again next time."""
    from app.services.storage_write_admission import StorageWritesPaused, background_write

    try:
        async with background_write("QC metrics cache"):
            await adapter.write_text(cache_uri, json.dumps(metrics, indent=2), content_type="application/json")
    except StorageWritesPaused:
        logger.info("QC metrics cache not written: storage writes are paused")
