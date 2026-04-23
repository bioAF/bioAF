import logging

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.file import File
from app.models.plot_archive_entry import PlotArchiveEntry
from app.services.audit_service import log_action

logger = logging.getLogger("bioaf.file_service")


class FileService:
    @staticmethod
    async def create_file_record(
        session: AsyncSession,
        org_id: int,
        user_id: int | None,
        filename: str,
        gcs_uri: str,
        size_bytes: int | None,
        md5_checksum: str | None,
        file_type: str,
        tags: list[str] | None = None,
        project_id: int | None = None,
        experiment_id: int | None = None,
        source_type: str = "upload",
        source_pipeline_run_id: int | None = None,
        artifact_type: str | None = None,
    ) -> File:
        file = File(
            organization_id=org_id,
            gcs_uri=gcs_uri,
            filename=filename,
            size_bytes=size_bytes,
            md5_checksum=md5_checksum,
            uploader_user_id=user_id,
            file_type=file_type,
            tags_json=tags or [],
            project_id=project_id,
            experiment_id=experiment_id,
            source_type=source_type,
            source_pipeline_run_id=source_pipeline_run_id,
            artifact_type=artifact_type,
        )
        session.add(file)
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="file",
            entity_id=file.id,
            action="create",
            details={"filename": filename, "file_type": file_type, "size_bytes": size_bytes},
        )
        return file

    @staticmethod
    async def get_file(session: AsyncSession, file_id: int, org_id: int) -> File | None:
        result = await session.execute(
            select(File).options(selectinload(File.uploader)).where(File.id == file_id, File.organization_id == org_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_files(
        session: AsyncSession,
        org_id: int,
        file_type: str | None = None,
        experiment_id: int | None = None,
        project_id: int | None = None,
        source_type: str | None = None,
        sample_id: int | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[list[File], int]:
        from app.models.sample import sample_files

        query = select(File).options(selectinload(File.uploader)).where(File.organization_id == org_id)
        count_query = select(func.count(File.id)).where(File.organization_id == org_id)

        if search:
            pattern = f"%{search}%"
            query = query.where(File.filename.ilike(pattern))
            count_query = count_query.where(File.filename.ilike(pattern))

        if file_type:
            query = query.where(File.file_type == file_type)
            count_query = count_query.where(File.file_type == file_type)

        if experiment_id is not None:
            query = query.where(File.experiment_id == experiment_id)
            count_query = count_query.where(File.experiment_id == experiment_id)

        if project_id is not None:
            # Include files directly on the project OR files whose experiment
            # belongs to the project (files inherit project via experiment).
            from app.models.experiment import Experiment
            from sqlalchemy import or_

            project_filter = or_(
                File.project_id == project_id,
                File.experiment_id.in_(select(Experiment.id).where(Experiment.project_id == project_id)),
            )
            query = query.where(project_filter)
            count_query = count_query.where(project_filter)

        if source_type:
            query = query.where(File.source_type == source_type)
            count_query = count_query.where(File.source_type == source_type)

        if sample_id is not None:
            query = query.join(sample_files, sample_files.c.file_id == File.id).where(
                sample_files.c.sample_id == sample_id
            )
            count_query = count_query.join(sample_files, sample_files.c.file_id == File.id).where(
                sample_files.c.sample_id == sample_id
            )

        total_result = await session.execute(count_query)
        total = total_result.scalar() or 0

        offset = (page - 1) * page_size
        query = query.order_by(File.created_at.desc()).offset(offset).limit(page_size)

        result = await session.execute(query)
        files = list(result.scalars().all())

        return files, total

    @staticmethod
    async def get_sample_ids_for_files(session: AsyncSession, file_ids: list[int]) -> dict[int, list[int]]:
        """Return a mapping of file_id -> [sample_id, ...] for the given file IDs."""
        if not file_ids:
            return {}
        rows = await session.execute(
            text("SELECT file_id, sample_id FROM sample_files WHERE file_id = ANY(:ids)").bindparams(ids=file_ids)
        )
        result: dict[int, list[int]] = {fid: [] for fid in file_ids}
        for file_id, sample_id in rows.all():
            result[file_id].append(sample_id)
        return result

    @staticmethod
    async def link_file_to_sample(session: AsyncSession, file_id: int, sample_id: int) -> None:
        await session.execute(
            text(
                "INSERT INTO sample_files (sample_id, file_id) VALUES (:sample_id, :file_id) "
                "ON CONFLICT ON CONSTRAINT uq_sample_files_file_sample DO NOTHING"
            ),
            {"sample_id": sample_id, "file_id": file_id},
        )

    @staticmethod
    async def link_file_to_notebook_session(
        session: AsyncSession, file_id: int, session_id: int, access_type: str = "output"
    ) -> None:
        from sqlalchemy import text

        await session.execute(
            text(
                "INSERT INTO notebook_session_files (session_id, file_id, access_type) VALUES (:session_id, :file_id, :access_type)"
            ),
            {"session_id": session_id, "file_id": file_id, "access_type": access_type},
        )

    @staticmethod
    async def delete_file_record(session: AsyncSession, file_id: int, org_id: int, user_id: int) -> bool:
        file = await FileService.get_file(session, file_id, org_id)
        if not file:
            return False

        await log_action(
            session,
            user_id=user_id,
            entity_type="file",
            entity_id=file_id,
            action="delete",
            details={"filename": file.filename},
        )

        # Clean up any associated plot thumbnails from GCS before removing entries
        plot_entries = (
            (
                await session.execute(
                    select(PlotArchiveEntry.thumbnail_gcs_uri).where(
                        PlotArchiveEntry.file_id == file_id,
                        PlotArchiveEntry.thumbnail_gcs_uri.isnot(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        if plot_entries:
            from app.services.thumbnail_service import ThumbnailService

            for thumb_uri in plot_entries:
                await ThumbnailService.delete_thumbnail(session, thumb_uri)

        # Remove dependent rows from tables with FK references to files.id
        await session.execute(delete(PlotArchiveEntry).where(PlotArchiveEntry.file_id == file_id))

        from app.models.cellxgene_publication import CellxgenePublication
        from app.models.document import Document
        from app.models.file_parse_result import FileParseResult
        from app.models.ingest_event import IngestEvent
        from app.models.sample import sample_files

        await session.execute(sample_files.delete().where(sample_files.c.file_id == file_id))
        await session.execute(delete(FileParseResult).where(FileParseResult.file_id == file_id))
        await session.execute(delete(IngestEvent).where(IngestEvent.file_id == file_id))
        await session.execute(delete(CellxgenePublication).where(CellxgenePublication.file_id == file_id))
        await session.execute(delete(Document).where(Document.file_id == file_id))

        await session.execute(
            text("UPDATE analysis_snapshots SET figure_file_id = NULL WHERE figure_file_id = :fid").bindparams(
                fid=file_id
            )
        )
        await session.execute(
            text("UPDATE analysis_snapshots SET checkpoint_file_id = NULL WHERE checkpoint_file_id = :fid").bindparams(
                fid=file_id
            )
        )

        from app.models.notebook_session_file import NotebookSessionFile

        await session.execute(delete(NotebookSessionFile).where(NotebookSessionFile.file_id == file_id))

        # Detach from manifest entries (keep entries for audit, just unlink the file)
        await session.execute(
            text("UPDATE manifest_entries SET file_id = NULL WHERE file_id = :fid").bindparams(fid=file_id)
        )

        await session.delete(file)
        await session.flush()
        return True
