"""Free the downloaded data of a validation study nobody came back to retry.

A study in `error` stopped on infrastructure, not on its paper, and everything it downloaded is kept
so a retry can reuse it rather than pay for the download twice. Study 11 has held **122 GB** that
way since 2026-08-25, and nothing was ever going to delete it: `work_dir_reaper` reaps runs that
themselves failed, while the fetch is a *completed* run whose FASTQ are published to the results
bucket. Neither its outputs nor its work dir were in any reaper's scope, and the bucket lifecycle
rules only re-class to NEARLINE.

The window is `VALIDATION_FETCH_RETENTION_DAYS` from the moment the study stopped, which
`record_study_error` stamps and the study page renders as a date. Past it, three things go together:

- the FASTQ the fetch published (found by ``File.source_pipeline_run_id``, which the fetchngs ingest
  sets on every file it registers, so this deletes exactly what bioAF knows it fetched);
- that run's Nextflow work dir, which holds a second copy of the same downloads;
- **the rows**. ``retry_study`` decides where a retry resumes from a DB query, so a reap that leaves
  the sample-file links behind sends the retry to `setup` to relaunch against files that are gone,
  with no way back. Unlinked, the same retry lands at `plan_ready`, where a human decides whether to
  pay for the download again.

The `Sample` rows stay: they are the experiment's record of what was studied, and a sample with no
files is precisely what "nothing fetched" means to the retry path.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.registry import get_storage_adapter
from app.models.file import File
from app.models.sample import sample_files
from app.models.validation_study import ValidationStudy
from app.platform.work_dir_layout import work_dir_key
from app.services.validation_study_service import VALIDATION_FETCH_RETENTION_DAYS

logger = logging.getLogger(__name__)

_REAPED_KEY = "fetch_reaped"
_DEADLINE_KEY = "fetch_reap_after"

__all__ = ["VALIDATION_FETCH_RETENTION_DAYS", "ValidationFetchReaper"]


def _parse_iso(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class ValidationFetchReaper:
    @staticmethod
    async def reap(session: AsyncSession, raw_bucket: str) -> list[int]:
        """Reap the fetched data of every stopped study past its retry window.

        Returns the study ids actually reaped. A study is marked only once every object is gone, so
        a transient storage error leaves it to the next pass rather than stranding the data with
        nothing left to point at it.
        """
        now = datetime.now(timezone.utc)
        studies = list(
            (await session.execute(select(ValidationStudy).where(ValidationStudy.state == "error"))).scalars().all()
        )

        storage = get_storage_adapter()
        reaped: list[int] = []

        for study in studies:
            evidence = dict(study.evidence_json or {})
            if evidence.get(_REAPED_KEY):
                continue
            # No stamp, no proof of when the window opened. A study that stopped before this shipped
            # is left alone deliberately: deleting a fetch somebody still intends to resume must be
            # a decision, not a side effect of a deploy.
            deadline = _parse_iso(evidence.get(_DEADLINE_KEY) or "")
            if deadline is None or deadline > now:
                continue
            if study.data_run_id is None:
                continue  # never fetched anything; there is no window to close

            files = list(
                (await session.execute(select(File).where(File.source_pipeline_run_id == study.data_run_id)))
                .scalars()
                .all()
            )
            uris = [f.storage_uri or f.gcs_uri for f in files]
            freed_bytes = sum(f.size_bytes or 0 for f in files)

            try:
                for uri in uris:
                    if uri:
                        await storage.delete(uri)
                work_dir = storage.build_uri(raw_bucket, work_dir_key(study.data_run_id))
                objects = await storage.list_objects(work_dir) or []
                for obj in objects:
                    await storage.delete(obj.uri)
            except Exception as exc:
                # Deliberately unmarked: the data still exists, so the next pass must try again.
                logger.warning("validation study %d: fetch reap failed: %s", study.id, exc)
                continue

            file_ids = [f.id for f in files]
            if file_ids:
                await session.execute(sample_files.delete().where(sample_files.c.file_id.in_(file_ids)))
                await session.execute(delete(File).where(File.id.in_(file_ids)))

            evidence[_REAPED_KEY] = {
                "at": now.isoformat(),
                "objects": len(uris) + len(objects),
                "bytes": freed_bytes,
            }
            # A fresh dict: evidence_json is a plain (non-Mutable) JSONB column, so an in-place
            # mutation of the same reference is not tracked and the stamp would be lost.
            study.evidence_json = evidence
            await session.flush()

            reaped.append(study.id)
            logger.info(
                "validation study %d: reaped %d fetched file(s) and its work dir (%d bytes freed)",
                study.id,
                len(uris),
                freed_bytes,
            )

        return reaped
