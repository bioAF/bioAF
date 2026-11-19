import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import cast

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sqlalchemy.exc import IntegrityError

from app.exceptions import ConflictError, StorageUnavailableError, ValidationError
from app.models.file import File
from app.models.plot_archive_entry import PlotArchiveEntry
from app.services.audit_service import log_action

logger = logging.getLogger("bioaf.file_service")

EXTERNAL_ID_MAX_LENGTH = 255


class SourceIdentityAmbiguous(ConflictError):
    """More than one legacy File claims one pipeline output; nothing is registered until reconciled."""

    code = "source_identity_ambiguous"

    def __init__(self) -> None:
        super().__init__("source_identity_ambiguous", plain_body=True)


def validate_external_id(value: str | None) -> str | None:
    """An opaque caller identifier: 1-255 characters, kept exactly as supplied, or None."""
    if value is None:
        return None
    if not isinstance(value, str) or not 1 <= len(value) <= EXTERNAL_ID_MAX_LENGTH:
        raise ValidationError(f"external_id must be 1-{EXTERNAL_ID_MAX_LENGTH} characters")
    return value


def _conflict_for(exc: IntegrityError) -> ConflictError | None:
    message = str(exc.orig)
    if "uq_files_org_external_id" in message:
        return ConflictError("external_id_already_exists", plain_body=True)
    if "uq_files_pipeline_source_identity" in message:
        return ConflictError("source_identity_conflict", plain_body=True)
    return None


class ReadinessRefused(ValidationError):
    """The file cannot become ready: it is deleted, its bytes are gone, or it is not this organization's."""

    code = "readiness_refused"


_REGISTERED_KEY = "bioaf_registered_file_ids"

# Verification states that let readiness proceed. Anything else (waiting for an expectation,
# queued/preparing/verifying, mismatch, failure) keeps the file unready.
_READY_STATES = frozenset({"not_required", "verified"})


class FileService:
    @staticmethod
    async def create_file_record(
        session: AsyncSession,
        org_id: int,
        user_id: int | None,
        filename: str,
        storage_uri: str,
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
        external_id: str | None = None,
        source_storage_uri: str | None = None,
        source_notebook_session_id: int | None = None,
    ) -> File:
        """Create one File row on the caller's session (the caller commits).

        ``external_id`` is the caller's own identifier, or None. A pipeline output records where it
        was found as its durable source identity (``source_storage_uri``, by default the URI it is
        registered at). A duplicate external ID or source identity raises ConflictError and leaves
        the caller's transaction usable: the insert runs in a savepoint, so the unique indexes decide
        races without poisoning the session.
        """
        external_id = validate_external_id(external_id)
        if source_type == "pipeline_output" and source_pipeline_run_id is not None and source_storage_uri is None:
            source_storage_uri = storage_uri
        file = File(
            organization_id=org_id,
            storage_uri=storage_uri,
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
            external_id=external_id,
            source_storage_uri=source_storage_uri,
            source_notebook_session_id=source_notebook_session_id,
        )
        try:
            async with session.begin_nested():
                session.add(file)
                await session.flush()
        except IntegrityError as exc:
            conflict = _conflict_for(exc)
            if conflict is None:
                raise
            raise conflict from None

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
    async def finalize_registration(session: AsyncSession, file: File) -> None:
        """Announce a newly created File once its initial context is established.

        Call after the registration workflow has set the file's external ID, experiment/project
        context, sample links, and any required move, on the session that created it. The
        ``file.registered`` snapshot is taken now and deferred until that transaction commits; a
        second call for the same file in the same transaction does nothing. Retries and adoption of
        an existing File must not call this.
        """
        from app.services import entity_references, webhook_events
        from app.services.event_types import INTEGRATION_FILE_REGISTERED
        from app.services.post_commit_events import defer_event

        announced: set[int] = session.sync_session.info.setdefault(_REGISTERED_KEY, set())
        if file.id in announced:
            return
        await session.flush()
        relations = await entity_references.file_relationships(session, file)
        defer_event(session, INTEGRATION_FILE_REGISTERED, webhook_events.file_registered(file, relations))
        announced.add(file.id)

    @staticmethod
    async def mark_ready(
        session: AsyncSession,
        file_id: int,
        org_id: int,
        *,
        expected_size: int | None = None,
    ) -> str:
        """Set ``ready_at`` once the final stored bytes are verified available; the single owner.

        Returns ``ready`` (this call made the transition and deferred ``file.ready``),
        ``already_ready``, ``pending`` (a required checksum or expectation is not yet verified for
        the current object), or ``failed`` (the object is missing, unreadable, or the wrong size;
        recorded on the file's verification record so users and API consumers can see it).

        The file row is locked for the decision, so concurrent callers produce one transition. The
        only I/O is one metadata read of the file's own registered URI; hashing never happens here
        (FileVerificationService owns that, outside requests and long transactions). Runs on the
        caller's transaction and never commits.
        """
        from app.adapters.models import StorageObjectNotFound
        from app.adapters.registry import get_storage_adapter
        from app.models.file_verification import FileVerification
        from app.services import entity_references, webhook_events
        from app.services.event_types import INTEGRATION_FILE_READY
        from app.services.post_commit_events import defer_event

        file = (
            await session.execute(
                select(File).where(File.id == file_id, File.organization_id == org_id).with_for_update()
            )
        ).scalar_one_or_none()
        if file is None:
            raise ReadinessRefused("file_not_found")
        if file.deleted_at is not None or file.storage_deleted:
            raise ReadinessRefused("file_deleted")
        await session.refresh(file)
        if file.ready_at is not None:
            return "already_ready"

        record = (
            await session.execute(select(FileVerification).where(FileVerification.file_id == file.id).with_for_update())
        ).scalar_one_or_none()
        uri = file.storage_uri or file.gcs_uri
        if record is not None and record.status not in _READY_STATES:
            return "pending"
        if record is not None and record.status == "verified" and record.observed_uri != uri:
            # A proof for another object (the file moved since) proves nothing about this one.
            return "pending"

        async def _fail(code: str, retryable: bool) -> str:
            nonlocal record
            if record is None:
                # No record means no checksum source applies (mark_ready is only reached without one).
                record = FileVerification(
                    organization_id=file.organization_id, file_id=file.id, status="failed", expectation_resolved=True
                )
                session.add(record)
            record.status = "failed"
            record.error_code = code
            record.retryable = retryable
            await session.flush()
            logger.warning("File %s could not be made ready: %s", file.id, code)
            return "failed"

        try:
            observed = await get_storage_adapter().object_identity(uri)
        except StorageObjectNotFound:
            return await _fail("object_missing", True)
        except Exception:
            return await _fail("storage_unavailable", True)
        size = observed.size_bytes
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            return await _fail("size_unavailable", True)
        if expected_size is not None and size != expected_size:
            return await _fail("size_mismatch", False)
        if record is not None and record.status == "verified":
            stale = record.observed_bytes not in (None, size) or (
                record.observed_object_version is not None and observed.version != record.observed_object_version
            )
            if stale:
                # The object changed after it was proved: the old evidence cannot carry over.
                record.status = "queued"
                record.observed_checksum = None
                record.verified_at = None
                record.next_attempt_at = datetime.now(timezone.utc)
                await session.flush()
                return "pending"

        file.size_bytes = size
        if record is not None and record.status == "verified":
            file.md5_checksum = record.observed_checksum
        file.ready_at = datetime.now(timezone.utc)
        await session.flush()
        relations = await entity_references.file_relationships(session, file)
        defer_event(session, INTEGRATION_FILE_READY, webhook_events.file_ready(file, relations))
        return "ready"

    @staticmethod
    async def find_registered_outputs(
        session: AsyncSession, org_id: int, run_id: int, uris: Sequence[str], *, ambiguous: set[str] | None = None
    ) -> dict[str, File]:
        """Files already registered for these pipeline-output URIs, in (organization, run) scope.

        A URI matches a File's durable source identity or its current canonical URI, so an original
        output still present after its bytes were relocated is the same File. Deleted Files count:
        a rescan must not resurrect a deleted registration. Raises SourceIdentityAmbiguous when a URI
        matches more than one File (unreconciled legacy duplicates).
        """
        wanted = [u for u in dict.fromkeys(uris) if u]
        if not wanted:
            return {}
        rows = (
            (
                await session.execute(
                    select(File).where(
                        File.organization_id == org_id,
                        File.source_pipeline_run_id == run_id,
                        or_(File.source_storage_uri.in_(wanted), File.storage_uri.in_(wanted)),
                    )
                )
            )
            .scalars()
            .all()
        )
        found: dict[str, File] = {}
        for uri in wanted:
            matches = {f.id: f for f in rows if uri in (f.source_storage_uri, f.storage_uri)}
            if len(matches) > 1:
                if ambiguous is None:
                    raise SourceIdentityAmbiguous()
                ambiguous.add(uri)
                continue
            if matches:
                found[uri] = next(iter(matches.values()))
        return found

    @staticmethod
    async def get_file(
        session: AsyncSession, file_id: int, org_id: int, *, include_deleted: bool = False
    ) -> File | None:
        """One file, or None when it is gone.

        A deleted file reads as absent, which is what every caller already
        handles: deletion is SOFT, so the row and its identity survive, but a
        file somebody deleted must not come back through a listing, a download
        or a launch. ``include_deleted`` is for the paths whose subject IS the
        record (provenance, audit), not for working views.
        """
        query = (
            select(File).options(selectinload(File.uploader)).where(File.id == file_id, File.organization_id == org_id)
        )
        if not include_deleted:
            query = query.where(File.deleted_at.is_(None))
        result = await session.execute(query)
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
        from app.services import file_scope

        # Deleted files are absent from every listing. The row survives so its
        # identity keeps resolving and no provenance record dangles; that is a
        # different question from what a scientist should be offered here.
        query = select(File).options(selectinload(File.uploader)).where(file_scope.live_files(org_id))
        count_query = select(func.count(File.id)).where(file_scope.live_files(org_id))

        if search:
            pattern = f"%{search}%"
            query = query.where(File.filename.ilike(pattern))
            count_query = count_query.where(File.filename.ilike(pattern))

        if file_type:
            query = query.where(File.file_type == file_type)
            count_query = count_query.where(File.file_type == file_type)

        if experiment_id is not None:
            experiment_filter = file_scope.experiment_files(experiment_id, org_id)
            query = query.where(experiment_filter)
            count_query = count_query.where(experiment_filter)

        if project_id is not None:
            project_filter = file_scope.project_files(project_id, org_id)
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
        for file_id, sample_id in cast("Sequence[tuple[int, int]]", rows.all()):
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
        for fid, sid in cast("Sequence[tuple[int, int]]", sample_link_rows):
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
            return sample.external_id or f"Sample {sample.id}"

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
    async def sample_in_org(session: AsyncSession, sample_id: int, org_id: int) -> bool:
        row = await session.execute(
            text(
                "SELECT 1 FROM samples s JOIN experiments e ON e.id = s.experiment_id "
                "WHERE s.id = :sample_id AND e.organization_id = :org_id"
            ),
            {"sample_id": sample_id, "org_id": org_id},
        )
        return row.first() is not None

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
    async def _free_storage(session: AsyncSession, file: File) -> bool:
        """Erase the object behind ``file``. Returns whether bytes were freed.

        Deletion has to reclaim the space or it is not deletion (issue #86): a
        scientist deleting a 40 GB BAM is buying back disk, and a catalogue that
        only hides the row goes on billing them for it.

        Two cases free nothing and are not failures. There may be no object left
        (``storage_deleted`` already set by a stack teardown, or no URI at all),
        and the object may still belong to another live catalogue entry, whose
        owner asked for nothing to be deleted.
        """
        uri = file.storage_uri or file.gcs_uri
        if file.storage_deleted or not uri:
            return False

        still_needed = (
            await session.execute(
                select(func.count(File.id)).where(
                    or_(File.storage_uri == uri, File.gcs_uri == uri),
                    File.id != file.id,
                    File.deleted_at.is_(None),
                )
            )
        ).scalar()
        if still_needed:
            logger.info("File %s shares its object with %d live record(s); leaving the bytes", file.id, still_needed)
            return False

        from app.adapters.registry import get_storage_adapter

        try:
            # The adapter contract makes this idempotent: an object that is
            # already gone is not an error, so a record whose bytes vanished on
            # their own is never stuck undeletable.
            await get_storage_adapter().delete(uri)
        except Exception as exc:
            logger.exception("Could not delete object %s for file %s", uri, file.id)
            raise StorageUnavailableError(
                "The file's storage could not be reached, so nothing was deleted. Try again."
            ) from exc
        return True

    @staticmethod
    async def delete_file_record(session: AsyncSession, file_id: int, org_id: int, user_id: int) -> bool:
        file = await FileService.get_file(session, file_id, org_id)
        if not file:
            return False

        # Bytes first, record second. If storage fails the exception propagates
        # before anything is committed, so the file stays whole and listed
        # rather than reading as deleted while its object survives, which is the
        # exact state issue #86 exists to end.
        storage_freed = await FileService._free_storage(session, file)

        await log_action(
            session,
            user_id=user_id,
            entity_type="file",
            entity_id=file_id,
            action="delete",
            details={
                "filename": file.filename,
                "size_bytes": file.size_bytes,
                "storage_freed": storage_freed,
            },
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

        # SOFT for the RECORD, hard for the BYTES. The row and its `uuid` are
        # never removed: a catalogue number that stops resolving the moment
        # somebody tidies up is not a catalogue number, and a run that consumed
        # this file must go on being able to say so. The object itself is gone
        # by now, and `storage_deleted` is the tombstone saying so, so nothing
        # offers the scientist a download that cannot be served.
        #
        # The working rows above ARE still cleaned up. What survives is the
        # catalogue entry and the provenance that references it.
        file.deleted_at = datetime.now(timezone.utc)
        file.deleted_by_user_id = user_id
        if storage_freed:
            file.storage_deleted = True
        await session.flush()
        return True
