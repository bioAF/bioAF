import logging
from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.file import File
from app.models.plot_archive_entry import PlotArchiveEntry
from app.services.audit_service import log_action
from app.services.file_service import FileService

logger = logging.getLogger("bioaf.plot_archive_service")

# Track last scan timestamp per org
_last_scan: dict[int, datetime] = {}


class PlotArchiveService:
    @staticmethod
    async def index_plot(
        session: AsyncSession,
        org_id: int,
        file_id: int,
        experiment_id: int | None = None,
        pipeline_run_id: int | None = None,
        notebook_session_id: int | None = None,
        title: str | None = None,
        tags: list[str] | None = None,
    ) -> PlotArchiveEntry:
        entry = PlotArchiveEntry(
            organization_id=org_id,
            file_id=file_id,
            experiment_id=experiment_id,
            pipeline_run_id=pipeline_run_id,
            notebook_session_id=notebook_session_id,
            title=title,
            tags_json=tags or [],
        )
        session.add(entry)
        await session.flush()
        return entry

    @staticmethod
    async def search_plots(
        session: AsyncSession,
        org_id: int,
        experiment_id: int | None = None,
        pipeline_run_id: int | None = None,
        query: str | None = None,
        tags: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[list[PlotArchiveEntry], int]:
        base = (
            select(PlotArchiveEntry)
            .options(selectinload(PlotArchiveEntry.file).selectinload(File.uploader))
            .where(PlotArchiveEntry.organization_id == org_id)
        )
        count_base = select(func.count(PlotArchiveEntry.id)).where(PlotArchiveEntry.organization_id == org_id)

        if experiment_id:
            base = base.where(PlotArchiveEntry.experiment_id == experiment_id)
            count_base = count_base.where(PlotArchiveEntry.experiment_id == experiment_id)
        if pipeline_run_id:
            base = base.where(PlotArchiveEntry.pipeline_run_id == pipeline_run_id)
            count_base = count_base.where(PlotArchiveEntry.pipeline_run_id == pipeline_run_id)
        if query:
            like = f"%{query}%"
            base = base.where(PlotArchiveEntry.title.ilike(like))
            count_base = count_base.where(PlotArchiveEntry.title.ilike(like))
        if date_from:
            base = base.where(PlotArchiveEntry.indexed_at >= date_from)
            count_base = count_base.where(PlotArchiveEntry.indexed_at >= date_from)
        if date_to:
            base = base.where(PlotArchiveEntry.indexed_at <= date_to)
            count_base = count_base.where(PlotArchiveEntry.indexed_at <= date_to)

        total_result = await session.execute(count_base)
        total = total_result.scalar() or 0

        offset = (page - 1) * page_size
        base = base.order_by(PlotArchiveEntry.indexed_at.desc()).offset(offset).limit(page_size)

        result = await session.execute(base)
        plots = list(result.scalars().all())
        return plots, total

    @staticmethod
    async def get_plot(session: AsyncSession, org_id: int, plot_id: int) -> PlotArchiveEntry | None:
        result = await session.execute(
            select(PlotArchiveEntry)
            .options(selectinload(PlotArchiveEntry.file).selectinload(File.uploader))
            .where(PlotArchiveEntry.id == plot_id, PlotArchiveEntry.organization_id == org_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def update_plot(
        session: AsyncSession,
        org_id: int,
        plot_id: int,
        user_id: int,
        title: str | None = None,
        tags: list[str] | None = None,
    ) -> PlotArchiveEntry | None:
        plot = await PlotArchiveService.get_plot(session, org_id, plot_id)
        if not plot:
            return None

        if title is not None:
            plot.title = title
        if tags is not None:
            plot.tags_json = tags

        await log_action(
            session,
            user_id=user_id,
            entity_type="plot_archive",
            entity_id=plot_id,
            action="update",
            details={"title": title, "tags": tags},
        )
        await session.flush()
        return plot

    @staticmethod
    async def scan_and_index(session: AsyncSession) -> int:
        """Scan GCS results bucket for new image files and auto-index them."""
        indexed = 0
        try:
            from google.cloud import storage as gcs_storage

            client = gcs_storage.Client()

            # Get all organizations (simplified — in practice scope by active orgs)
            from app.models.organization import Organization

            orgs_result = await session.execute(select(Organization))
            orgs = list(orgs_result.scalars().all())

            cfg_result = await session.execute(
                text("SELECT value FROM platform_config WHERE key = 'results_bucket_name'")
            )
            results_bucket = cfg_result.scalar_one_or_none()
            if not results_bucket or results_bucket == "null":
                logger.warning("results_bucket_name not configured in platform_config, skipping plot scan")
                return 0

            for org in orgs:
                org_id = org.id
                bucket_name = results_bucket
                last = _last_scan.get(org_id)

                try:
                    bucket = client.bucket(bucket_name)
                    blobs = bucket.list_blobs()

                    for blob in blobs:
                        # Filter image files
                        if not blob.name.lower().endswith((".png", ".svg", ".pdf")):
                            continue

                        # Skip if already indexed
                        if last and blob.updated and blob.updated < last:
                            continue

                        # Check not already in archive
                        gcs_uri = f"gs://{bucket_name}/{blob.name}"
                        existing = await session.execute(select(File.id).where(File.gcs_uri == gcs_uri))
                        if existing.scalar_one_or_none():
                            continue

                        # Determine file type
                        ext = blob.name.rsplit(".", 1)[-1].lower()
                        file_type = ext if ext in ("png", "svg", "pdf") else "other"

                        # Extract context from path
                        experiment_id, pipeline_run_id = PlotArchiveService._parse_ids_from_path(gcs_uri)

                        # Create file record
                        file = await FileService.create_file_record(
                            session,
                            org_id=org_id,
                            user_id=None,
                            filename=blob.name.split("/")[-1],
                            gcs_uri=gcs_uri,
                            size_bytes=blob.size,
                            md5_checksum=None,
                            file_type=file_type,
                        )

                        # Create archive entry
                        await PlotArchiveService.index_plot(
                            session,
                            org_id=org_id,
                            file_id=file.id,
                            experiment_id=experiment_id,
                            pipeline_run_id=pipeline_run_id,
                            title=blob.name.split("/")[-1],
                        )
                        indexed += 1

                except Exception as e:
                    logger.warning("Failed to scan bucket for org %d: %s", org_id, e)

                _last_scan[org_id] = datetime.now(timezone.utc)

            if indexed > 0:
                await session.commit()
                logger.info("Plot archive watcher indexed %d new plots", indexed)

        except ImportError:
            logger.debug("google-cloud-storage not installed, plot watcher inactive")
        except Exception as e:
            logger.error("Plot archive scan failed: %s", e)

        return indexed

    @staticmethod
    def _parse_ids_from_path(gcs_uri: str) -> tuple[int | None, int | None]:
        """Extract experiment_id and pipeline_run_id from a GCS URI path."""
        parts = gcs_uri.replace("gs://", "").split("/")
        experiment_id = None
        pipeline_run_id = None
        for i, part in enumerate(parts):
            if part == "experiments" and i + 1 < len(parts):
                try:
                    experiment_id = int(parts[i + 1])
                except ValueError:
                    pass
            if part in ("runs", "pipeline-runs") and i + 1 < len(parts):
                try:
                    pipeline_run_id = int(parts[i + 1])
                except ValueError:
                    pass
        return experiment_id, pipeline_run_id

    @staticmethod
    async def backfill_metadata(session: AsyncSession) -> int:
        """Re-parse GCS URIs to correct experiment_id and pipeline_run_id on all entries."""
        from app.models.experiment import Experiment
        from app.models.pipeline_run import PipelineRun

        result = await session.execute(select(PlotArchiveEntry).options(selectinload(PlotArchiveEntry.file)))
        entries = list(result.scalars().all())
        updated = 0
        for entry in entries:
            if not entry.file or not entry.file.gcs_uri:
                continue
            exp_id, run_id = PlotArchiveService._parse_ids_from_path(entry.file.gcs_uri)
            changed = False
            if exp_id is not None and entry.experiment_id != exp_id:
                exists = await session.execute(select(Experiment.id).where(Experiment.id == exp_id))
                if exists.scalar_one_or_none() is not None:
                    entry.experiment_id = exp_id
                    changed = True
            if run_id is not None and entry.pipeline_run_id != run_id:
                exists = await session.execute(select(PipelineRun.id).where(PipelineRun.id == run_id))
                if exists.scalar_one_or_none() is not None:
                    entry.pipeline_run_id = run_id
                    changed = True
            if changed:
                updated += 1
        if updated > 0:
            await session.commit()
            logger.info("Backfilled metadata for %d plot archive entries", updated)
        return updated
