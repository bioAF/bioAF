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
        experiment_id: int | None = None,
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
            experiment_id=experiment_id,
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
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[list[File], int]:
        query = select(File).options(selectinload(File.uploader)).where(File.organization_id == org_id)
        count_query = select(func.count(File.id)).where(File.organization_id == org_id)

        if file_type:
            query = query.where(File.file_type == file_type)
            count_query = count_query.where(File.file_type == file_type)

        if experiment_id is not None:
            query = query.where(File.experiment_id == experiment_id)
            count_query = count_query.where(File.experiment_id == experiment_id)

        total_result = await session.execute(count_query)
        total = total_result.scalar() or 0

        offset = (page - 1) * page_size
        query = query.order_by(File.created_at.desc()).offset(offset).limit(page_size)

        result = await session.execute(query)
        files = list(result.scalars().all())

        return files, total

    @staticmethod
    async def link_file_to_sample(session: AsyncSession, file_id: int, sample_id: int) -> None:
        from sqlalchemy import text

        await session.execute(
            text("INSERT INTO sample_files (sample_id, file_id) VALUES (:sample_id, :file_id)"),
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

        # notebook_session_files is created by migration, not ORM metadata;
        # check existence at runtime before attempting cleanup
        table_check = await session.execute(
            text("SELECT 1 FROM information_schema.tables WHERE table_name = 'notebook_session_files' LIMIT 1")
        )
        if table_check.scalar_one_or_none():
            await session.execute(
                text("DELETE FROM notebook_session_files WHERE file_id = :fid").bindparams(fid=file_id)
            )

        await session.delete(file)
        await session.flush()
        return True
