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

Nothing is deleted until everything is authorized. The fetch run must be this study's, in its
organization and experiment; every registered File URI must lie inside that run's own output
location (`RunOutputLocationService`), and every work-dir object inside its own `run-<id>/`. A row
is history, not authority: a foreign or unverifiable URI flags the study (`fetch_reap_blocked`) and
it is skipped whole, safe objects included, with its rows left for diagnosis.

Storage and the database cannot change atomically, so the order is chosen for what a failure leaves
behind: the sample links go first and are committed (a retry can no longer treat the FASTQ as
runnable), then the objects, then the File rows and the completion marker. `fetch_reap_pending`
records the obligation between the two, keyed by the fetch run, and is finished on a later pass
even if the study has been retried out of `error` in the meantime.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.registry import get_storage_adapter
from app.models.file import File
from app.models.pipeline_run import PipelineRun
from app.models.sample import sample_files
from app.models.validation_study import ValidationStudy
from app.platform.run_output_layout import directory_prefix, uri_within
from app.platform.work_dir_layout import work_dir_key
from app.services.run_output_location_service import OutputLocationRefused, RunOutputLocationService
from app.services.validation_study_service import VALIDATION_FETCH_RETENTION_DAYS

logger = logging.getLogger(__name__)

_REAPED_KEY = "fetch_reaped"
_DEADLINE_KEY = "fetch_reap_after"
_PENDING_KEY = "fetch_reap_pending"
_BLOCKED_KEY = "fetch_reap_blocked"

__all__ = ["VALIDATION_FETCH_RETENTION_DAYS", "ValidationFetchReaper"]


def _parse_iso(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class _Blocked(Exception):
    def __init__(self, reason: str, uris: list[str] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.uris = uris or []


class ValidationFetchReaper:
    @staticmethod
    async def reap(session: AsyncSession, raw_bucket: str) -> list[int]:
        """Reap the fetched data of every stopped study past its retry window.

        Returns the study ids actually reaped. A study is marked only once every authorized object
        and its rows are gone; a study whose data cannot be authorized is flagged and left whole.
        Commits at each study's boundaries (see the module docstring for why the unlink must be
        durable before deletion); the scheduled job's own commit afterwards finds nothing left.
        """
        now = datetime.now(timezone.utc)
        studies = list(
            (
                await session.execute(
                    select(ValidationStudy).where(
                        or_(
                            ValidationStudy.state == "error",
                            ValidationStudy.evidence_json.has_key(_PENDING_KEY),
                        )
                    )
                )
            )
            .scalars()
            .all()
        )

        storage = get_storage_adapter()
        reaped: list[int] = []

        for study in studies:
            evidence = dict(study.evidence_json or {})
            pending = evidence.get(_PENDING_KEY)
            if pending:
                # An obligation already begun is finished regardless of the study's state or window.
                run_id = pending.get("run_id")
            else:
                done = evidence.get(_REAPED_KEY)
                if done and done.get("run_id") in (None, study.data_run_id):
                    continue
                # No stamp, no proof of when the window opened. A study that stopped before this
                # shipped is left alone deliberately: deleting a fetch somebody still intends to
                # resume must be a decision, not a side effect of a deploy.
                deadline = _parse_iso(evidence.get(_DEADLINE_KEY) or "")
                if deadline is None or deadline > now:
                    continue
                run_id = study.data_run_id
                if run_id is None:
                    continue  # never fetched anything; there is no window to close

            try:
                files, uris, objects = await ValidationFetchReaper._authorize(
                    session, study, run_id, storage, raw_bucket
                )
            except _Blocked as blocked:
                logger.warning("validation study %d: fetch reap refused: %s", study.id, blocked.reason)
                evidence[_BLOCKED_KEY] = {"at": now.isoformat(), "reason": blocked.reason, "uris": blocked.uris}
                study.evidence_json = evidence
                await session.commit()
                continue
            except Exception as exc:
                logger.warning("validation study %d: fetch reap could not start: %s", study.id, exc)
                continue

            file_ids = [f.id for f in files]
            freed_bytes = sum(f.size_bytes or 0 for f in files)

            # 1. Unlink, durably, before anything irreversible: from here a retry lands at the
            #    approval gate rather than relaunching against FASTQ that may be gone.
            links = []
            if file_ids:
                links = [
                    {"sample_id": sample_id, "file_id": file_id}
                    for sample_id, file_id in (
                        await session.execute(
                            select(sample_files.c.sample_id, sample_files.c.file_id).where(
                                sample_files.c.file_id.in_(file_ids)
                            )
                        )
                    ).all()
                ]
                await session.execute(sample_files.delete().where(sample_files.c.file_id.in_(file_ids)))
            evidence.pop(_BLOCKED_KEY, None)
            evidence[_PENDING_KEY] = {"at": now.isoformat(), "run_id": run_id}
            study.evidence_json = evidence
            await session.commit()

            # 2. The objects. Deletion is idempotent, so a later pass repeats it safely.
            deleted = 0
            try:
                for uri in uris + [o.storage_uri for o in objects]:
                    await storage.delete(uri)
                    deleted += 1
                # 3. The rows that pointed at them, then the marker.
                await ValidationFetchReaper._forget_files(session, file_ids)
            except Exception as exc:
                # Deliberately unmarked: the rows still name what is left, and the pending
                # obligation brings the next pass back here.
                logger.warning("validation study %d: fetch reap incomplete, will retry: %s", study.id, exc)
                await session.rollback()
                if deleted == 0 and not pending:
                    # Nothing is gone yet, so the data is still whole: put the links back and
                    # withdraw the obligation, as if this pass had not started.
                    await ValidationFetchReaper._restore(session, study, links)
                continue

            evidence = dict(study.evidence_json or {})
            evidence.pop(_PENDING_KEY, None)
            evidence[_REAPED_KEY] = {
                "at": now.isoformat(),
                "run_id": run_id,
                "objects": len(uris) + len(objects),
                "bytes": freed_bytes,
            }
            # A fresh dict: evidence_json is a plain (non-Mutable) JSONB column, so an in-place
            # mutation of the same reference is not tracked and the stamp would be lost.
            study.evidence_json = evidence
            # Each study's outcome is committed on its own, so a rollback for a later study can
            # never undo an earlier one's completion.
            await session.commit()

            reaped.append(study.id)
            logger.info(
                "validation study %d: reaped %d fetched file(s) and its work dir (%d bytes freed)",
                study.id,
                len(uris),
                freed_bytes,
            )

        return reaped

    @staticmethod
    async def _authorize(session: AsyncSession, study, run_id: int, storage, raw_bucket: str):
        """Every File URI and work object the study's cleanup would delete, or ``_Blocked``.

        Read-only: listing the work directory is the only storage call.
        """
        run = await session.get(PipelineRun, run_id)
        if run is None:
            raise _Blocked(f"fetch run {run_id} does not exist")
        if run.organization_id != study.organization_id or run.experiment_id != study.experiment_id:
            raise _Blocked(f"fetch run {run_id} is not this study's (organization or experiment differs)")
        try:
            outdir = await RunOutputLocationService.authorized_uri(session, run, storage)
        except OutputLocationRefused as exc:
            raise _Blocked(str(exc)) from exc

        files = list((await session.execute(select(File).where(File.source_pipeline_run_id == run_id))).scalars().all())
        foreign_rows = [f.id for f in files if f.organization_id != study.organization_id]
        if foreign_rows:
            raise _Blocked(f"files {foreign_rows} belong to another organization")
        uris = [u for u in (f.storage_uri or f.gcs_uri for f in files) if u]
        outside = [u for u in uris if not uri_within(storage, u, outdir)]
        if outside:
            raise _Blocked("registered files lie outside the fetch run's output location", outside)

        work_dir = directory_prefix(storage.build_uri(raw_bucket, work_dir_key(run_id)))
        objects = await storage.list_objects(work_dir) or []
        stray = [o.storage_uri for o in objects if not uri_within(storage, o.storage_uri, work_dir)]
        if stray:
            raise _Blocked("the work-dir listing returned objects outside the run's work directory", stray)
        return files, uris, objects

    @staticmethod
    async def _restore(session: AsyncSession, study, links: list[dict]) -> None:
        if links:
            await session.execute(sample_files.insert(), links)
        evidence = dict(study.evidence_json or {})
        evidence.pop(_PENDING_KEY, None)
        study.evidence_json = evidence or None
        await session.commit()

    @staticmethod
    async def _forget_files(session: AsyncSession, file_ids: list[int]) -> None:
        if file_ids:
            await session.execute(sample_files.delete().where(sample_files.c.file_id.in_(file_ids)))
            await session.execute(delete(File).where(File.id.in_(file_ids)))
