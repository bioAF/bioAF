"""Delete the Nextflow work directories of abandoned pipeline runs.

The shared `nextflow-work` prefix in the raw bucket held 2.13 TB after five runs
and nothing ever removed any of it. A work dir holds every task's intermediates: decompressed
references, STAR indexes, BAMs, the Fusion cache. For a run that failed or was
cancelled it is garbage the moment the retry window closes, and it is billed every
month until someone notices.

Two days is the window. Long enough to retry a run or diagnose it from its
intermediates, short enough that a failed 122 GB study does not sit on disk
indefinitely.

Completed runs are deliberately out of scope: their published outputs are derived
from the work dir and deleting them is a different decision with a different blast
radius.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.registry import get_storage_adapter
from app.models.pipeline_run import PipelineRun
from app.platform.work_dir_layout import WORK_DIR_ROOT, work_dir_key

logger = logging.getLogger(__name__)

# How long an abandoned run's intermediates are kept. Retries and post-mortems both
# happen within a day or two; nothing needs them after that.
WORK_DIR_RETENTION_DAYS = 2

# Terminal states that mean the run produced nothing worth keeping. `completed` is
# absent on purpose.
ABANDONED_STATUSES = ("failed", "cancelled")

_REAPED_FLAG = "work_dir_reaped"

__all__ = ["WORK_DIR_RETENTION_DAYS", "WORK_DIR_ROOT", "WorkDirReaper", "work_dir_key"]


class WorkDirReaper:
    @staticmethod
    async def reap(session: AsyncSession, raw_bucket: str) -> list[int]:
        """Delete work dirs for abandoned runs past the retention window.

        Returns the run ids actually reaped. A run is only marked reaped once every
        object under its prefix is gone, so a transient storage error leaves it to be
        retried on the next pass rather than stranding the data forever.
        """
        if not raw_bucket:
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(days=WORK_DIR_RETENTION_DAYS)
        result = await session.execute(
            select(PipelineRun).where(
                PipelineRun.status.in_(ABANDONED_STATUSES),
                PipelineRun.completed_at.is_not(None),
                PipelineRun.completed_at < cutoff,
            )
        )
        candidates = [r for r in result.scalars().all() if not (r.provider_metadata or {}).get(_REAPED_FLAG)]

        storage = get_storage_adapter()
        reaped: list[int] = []

        for run in candidates:
            prefix = storage.build_uri(raw_bucket, work_dir_key(run.id))
            try:
                objects = await storage.list_objects(prefix)
                for obj in objects or []:
                    await storage.delete(obj.uri)
            except Exception as exc:
                # Deliberately not marked: the data still exists, so the next pass
                # must try again rather than lose track of it.
                logger.warning("Work dir reap failed for run %d: %s", run.id, exc)
                continue

            run.provider_metadata = {**(run.provider_metadata or {}), _REAPED_FLAG: True}
            reaped.append(run.id)
            logger.info("Reaped work dir for run %d (%d objects)", run.id, len(objects or []))

        return reaped
