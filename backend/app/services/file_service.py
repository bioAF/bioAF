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
        is_global: bool = False,
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
            is_global=is_global,
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
        from sqlalchemy import or_

        from app.models.experiment import Experiment
        from app.models.sample import Sample, sample_files

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
            # File belongs to the experiment if it is directly attached OR
            # if it is linked to a sample that belongs to the experiment.
            sample_ids_for_exp = select(Sample.id).where(Sample.experiment_id == experiment_id)
            files_via_samples = select(sample_files.c.file_id).where(sample_files.c.sample_id.in_(sample_ids_for_exp))
            experiment_filter = or_(
                File.experiment_id == experiment_id,
                File.id.in_(files_via_samples),
            )
            query = query.where(experiment_filter)
            count_query = count_query.where(experiment_filter)

        if project_id is not None:
            # File belongs to the project if it is directly attached OR if it
            # belongs to an experiment under the project OR if it is linked to
            # a sample whose experiment belongs to the project.
            experiments_in_project = select(Experiment.id).where(Experiment.project_id == project_id)
            sample_ids_in_project = select(Sample.id).where(Sample.experiment_id.in_(experiments_in_project))
            files_via_samples_in_project = select(sample_files.c.file_id).where(
                sample_files.c.sample_id.in_(sample_ids_in_project)
            )
            project_filter = or_(
                File.project_id == project_id,
                File.experiment_id.in_(experiments_in_project),
                File.id.in_(files_via_samples_in_project),
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
    async def get_provenance_for_files(session: AsyncSession, files: list[File]) -> dict[int, dict]:
        """Return a mapping of file_id -> provenance dict for breadcrumb display.

        Each value is a dict shaped like FileProvenance with project, experiment,
        sample labels, pipeline run, compute session, and resolved creator.
        Issued in batched queries so it scales with the page size, not the
        per-file fanout.
        """
        if not files:
            return {}

        from app.models.experiment import Experiment
        from app.models.notebook_session import ComputeSession
        from app.models.pipeline_run import PipelineRun
        from app.models.project import Project
        from app.models.sample import Sample
        from app.models.user import User

        file_ids = [f.id for f in files]

        # Sample IDs per file (via sample_files junction)
        sample_link_rows = (
            await session.execute(
                text("SELECT file_id, sample_id FROM sample_files WHERE file_id = ANY(:ids)").bindparams(ids=file_ids)
            )
        ).all()
        file_sample_ids: dict[int, list[int]] = {fid: [] for fid in file_ids}
        all_sample_ids: set[int] = set()
        for fid, sid in sample_link_rows:
            file_sample_ids[fid].append(sid)
            all_sample_ids.add(sid)

        sample_rows: dict[int, Sample] = {}
        if all_sample_ids:
            sample_result = await session.execute(select(Sample).where(Sample.id.in_(all_sample_ids)))
            sample_rows = {s.id: s for s in sample_result.scalars().all()}

        # Walk samples upward to fill missing experiment_id on files where we can
        explicit_experiment_ids: set[int] = {f.experiment_id for f in files if f.experiment_id is not None}
        sample_experiment_ids: set[int] = {s.experiment_id for s in sample_rows.values() if s.experiment_id is not None}
        all_experiment_ids = explicit_experiment_ids | sample_experiment_ids

        experiment_rows: dict[int, Experiment] = {}
        if all_experiment_ids:
            exp_result = await session.execute(select(Experiment).where(Experiment.id.in_(all_experiment_ids)))
            experiment_rows = {e.id: e for e in exp_result.scalars().all()}

        # Project IDs come from files directly OR from experiments
        explicit_project_ids: set[int] = {f.project_id for f in files if f.project_id is not None}
        experiment_project_ids: set[int] = {e.project_id for e in experiment_rows.values() if e.project_id is not None}
        all_project_ids = explicit_project_ids | experiment_project_ids

        project_rows: dict[int, Project] = {}
        if all_project_ids:
            project_result = await session.execute(select(Project).where(Project.id.in_(all_project_ids)))
            project_rows = {p.id: p for p in project_result.scalars().all()}

        # Pipeline runs
        run_ids: set[int] = {f.source_pipeline_run_id for f in files if f.source_pipeline_run_id is not None}
        run_rows: dict[int, PipelineRun] = {}
        if run_ids:
            run_result = await session.execute(select(PipelineRun).where(PipelineRun.id.in_(run_ids)))
            run_rows = {r.id: r for r in run_result.scalars().all()}

        # Compute sessions
        session_ids: set[int] = {
            f.source_notebook_session_id for f in files if f.source_notebook_session_id is not None
        }
        session_rows: dict[int, ComputeSession] = {}
        if session_ids:
            cs_result = await session.execute(select(ComputeSession).where(ComputeSession.id.in_(session_ids)))
            session_rows = {cs.id: cs for cs in cs_result.scalars().all()}

        # All users we need (uploaders + run launchers + session launchers)
        user_ids: set[int] = set()
        for f in files:
            if f.uploader_user_id is not None:
                user_ids.add(f.uploader_user_id)
        for r in run_rows.values():
            if r.submitted_by_user_id is not None:
                user_ids.add(r.submitted_by_user_id)
        for cs in session_rows.values():
            if cs.user_id is not None:
                user_ids.add(cs.user_id)

        user_rows: dict[int, User] = {}
        if user_ids:
            user_result = await session.execute(select(User).where(User.id.in_(user_ids)))
            user_rows = {u.id: u for u in user_result.scalars().all()}

        def _user_summary(user_id: int | None) -> dict | None:
            if user_id is None or user_id not in user_rows:
                return None
            u = user_rows[user_id]
            return {"id": u.id, "name": u.name, "email": u.email}

        def _sample_label(sample: Sample) -> str:
            return sample.sample_id_unique or f"Sample {sample.id}"

        def _session_kind_and_type(cs: ComputeSession) -> tuple[str, str | None]:
            if cs.session_type == "ssh":
                return "work_node", None
            if cs.session_type in ("rstudio", "jupyter"):
                return "notebook", cs.session_type
            return "notebook", cs.session_type

        result: dict[int, dict] = {}
        for f in files:
            # Resolve experiment: explicit on file, else inferred from any linked sample
            exp_id = f.experiment_id
            if exp_id is None:
                for sid in file_sample_ids.get(f.id, []):
                    s = sample_rows.get(sid)
                    if s and s.experiment_id is not None:
                        exp_id = s.experiment_id
                        break
            exp = experiment_rows.get(exp_id) if exp_id is not None else None

            # Resolve project: explicit on file, else inferred from experiment
            proj_id = f.project_id
            if proj_id is None and exp is not None:
                proj_id = exp.project_id
            proj = project_rows.get(proj_id) if proj_id is not None else None

            sample_labels = [
                _sample_label(sample_rows[sid]) for sid in file_sample_ids.get(f.id, []) if sid in sample_rows
            ]

            pipeline_run = None
            if f.source_pipeline_run_id and f.source_pipeline_run_id in run_rows:
                r = run_rows[f.source_pipeline_run_id]
                pipeline_run = {
                    "id": r.id,
                    "pipeline_name": r.pipeline_name,
                    "launcher": _user_summary(r.submitted_by_user_id),
                }

            compute_session = None
            if f.source_notebook_session_id and f.source_notebook_session_id in session_rows:
                cs = session_rows[f.source_notebook_session_id]
                kind, notebook_type = _session_kind_and_type(cs)
                compute_session = {
                    "id": cs.id,
                    "kind": kind,
                    "notebook_type": notebook_type,
                    "launcher": _user_summary(cs.user_id),
                }

            # Resolved creator: launcher if pipeline/session output, else uploader
            creator = None
            if pipeline_run and pipeline_run["launcher"]:
                creator = pipeline_run["launcher"]
            elif compute_session and compute_session["launcher"]:
                creator = compute_session["launcher"]
            else:
                creator = _user_summary(f.uploader_user_id)

            result[f.id] = {
                "project_id": proj.id if proj else None,
                "project_name": proj.name if proj else None,
                "experiment_id": exp.id if exp else None,
                "experiment_name": exp.name if exp else None,
                "sample_labels": sample_labels,
                "pipeline_run": pipeline_run,
                "compute_session": compute_session,
                "creator": creator,
            }

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
