import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import DomainError, StateError, UploadFailedError, ValidationError
from app.models.file import File
from app.models.upload_checkpoint import UploadCheckpoint
from app.services.event_types import DATA_UPLOADED
from app.services import sequencing_identity
from app.services.sequencing_enrichment import enrich_from_header
from app.services.entity_references import verify_association_targets
from app.services.file_service import FileService
from app.services.post_commit_events import defer_event

logger = logging.getLogger("bioaf.upload_service")

# Presigned upload URLs (issued before upload sessions) were valid this long. Expiry is not proof
# the upload finished; settlement waits a further hour (see upload_settlement).
SIGNED_URL_SECONDS = 3600
# Automatic filing retries after the first failure, then the upload waits for Retry now / Cancel.
RETRY_DELAYS = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15))
MAX_AUTOMATIC_ATTEMPTS = len(RETRY_DELAYS)
# While storage writes are paused, filing waits this long before trying again (not a failed attempt).
PAUSED_RETRY = timedelta(minutes=5)

# One reading of the Illumina convention, shared with the sample-sheet builder.
# These names are re-exported because callers already import them from here.
ILLUMINA_PATTERN = sequencing_identity.ILLUMINA_PATTERN
READ_TYPES = sequencing_identity.READ_TYPES


# What the caller is told when an upload fails unexpectedly. The transfer
# message promises nothing was saved: no file record exists yet at that stage.
_TRANSFER_FAILED = "Upload failed because storage did not respond. Nothing was saved. Try again. Reference {ref}."
_UPLOAD_FAILED = "Upload failed. Try again. Reference {ref}."


class UploadReceivedNotFiled(DomainError):
    """The bytes are stored but filing them failed; they stay tracked and filing is retried.

    Answered as 202 Accepted with the upload's reference (mockup M12), never the raw error.
    """

    status_code = 202
    code = "upload_received_not_filed"


def _reference() -> str:
    return f"UPL-{secrets.token_hex(3).upper()}"


def _session_factory():
    from app import database

    return database.async_session_factory


# First key of the per-upload filing lock (pg_advisory_xact_lock(namespace, hashtext(upload_id))).
_FILING_LOCK_NAMESPACE = 11_0006


async def _new_checkpoint(**fields) -> str:
    """Record an upload in its own committed transaction, before any byte may arrive."""
    fields.setdefault("reference", _reference())
    async with _session_factory()() as s:
        s.add(UploadCheckpoint(**fields))
        await s.commit()
    return fields["id"]


async def _update_checkpoint(upload_id: str, **fields) -> None:
    """Update an upload's checkpoint in its own committed transaction."""
    from sqlalchemy import update

    async with _session_factory()() as s:
        await s.execute(update(UploadCheckpoint).where(UploadCheckpoint.id == upload_id).values(**fields))
        await s.commit()


async def _load_checkpoint(session: AsyncSession, org_id: int, upload_id: str) -> UploadCheckpoint | None:
    checkpoint = await session.get(UploadCheckpoint, upload_id, populate_existing=True)
    if checkpoint is None or checkpoint.organization_id != org_id:
        return None
    return checkpoint


async def lock_upload(session: AsyncSession, upload_id: str) -> None:
    """Serialize completion, filing, cancellation and reconciliation of one upload.

    Held until the caller's transaction ends. An advisory lock rather than a row lock, so the
    holder can still record progress and failures on the checkpoint in separate transactions.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:ns, hashtext(:id))").bindparams(ns=_FILING_LOCK_NAMESPACE, id=upload_id)
    )


class UploadNotFinished(DomainError):
    """Completion was reported before the provider has the whole object."""

    status_code = 409
    code = "upload_not_finished"


class _FilingConflict(Exception):
    """Filing found storage it must not claim or overwrite: a destination it cannot prove is this
    upload's, a received source that changed, or a legacy plan whose bytes cannot be located safely.
    The bytes stay tracked (received, not filed) for an administrator; nothing is adopted or deleted."""

    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


async def _record_filing_failure(upload_id: str, exc: BaseException) -> int:
    """Count a failed filing attempt and schedule the next automatic one; return the attempt count.

    A conflict is not retried automatically (retrying cannot resolve it); it waits for Retry now or
    Cancel upload with its own category."""
    async with _session_factory()() as s:
        checkpoint = await s.get(UploadCheckpoint, upload_id, with_for_update=True)
        if checkpoint is None:
            return 0
        conflict = isinstance(exc, _FilingConflict)
        checkpoint.attempts = (checkpoint.attempts or 0) + 1
        if conflict:
            checkpoint.attempts = max(checkpoint.attempts, MAX_AUTOMATIC_ATTEMPTS + 1)
        due = checkpoint.attempts <= MAX_AUTOMATIC_ATTEMPTS
        checkpoint.next_attempt_at = datetime.now(timezone.utc) + RETRY_DELAYS[checkpoint.attempts - 1] if due else None
        checkpoint.last_error_category = exc.category if conflict else "filing_failed"
        attempts = checkpoint.attempts
        reference = checkpoint.reference
        await s.commit()
    # Only the reference, the attempt and the error type: storage errors can carry URLs.
    logger.error(
        "Upload received but not filed (reference=%s, attempt=%d, error_type=%s)",
        reference,
        attempts,
        type(exc).__name__,
    )
    return attempts


def _pending_message(reference: str, attempts: int, experiment: str | None = None) -> str:
    """The uploader's message for received bytes that are not filed yet (mockup M12)."""
    target = experiment or "its experiment"
    if attempts <= MAX_AUTOMATIC_ATTEMPTS:
        return (
            f"Received, not yet filed. Your file is stored safely but could not be added to {target} yet. "
            f"bioAF will try again automatically. Reference {reference}."
        )
    return (
        "Could not be filed. Your file is still stored. Retry now, or cancel the upload to remove it. "
        f"Reference {reference}."
    )


def upload_failure(stage: str, exc: BaseException) -> UploadFailedError:
    """Log an unexpected upload failure safely and return the error to raise.

    Only the stage and the error's type are logged: storage SDK messages can
    contain bucket URLs, signed URLs or credentials.
    """
    reference = f"UPL-{secrets.token_hex(3).upper()}"
    logger.error("Upload failed (reference=%s, stage=%s, error_type=%s)", reference, stage, type(exc).__name__)
    message = _TRANSFER_FAILED if stage == "transfer" else _UPLOAD_FAILED
    return UploadFailedError(message.format(ref=reference), details={"reference": reference})


class UploadService:
    @staticmethod
    def parse_illumina_filename(filename: str) -> dict | None:
        """Extract sample name, lane, read number, set number from Illumina filename."""
        return sequencing_identity.parse_illumina_filename(filename)

    @staticmethod
    def validate_fastq_filename(filename: str) -> bool:
        """Check if filename has valid FASTQ extension."""
        lower = filename.lower()
        return lower.endswith(".fastq.gz") or lower.endswith(".fq.gz")

    @staticmethod
    async def _get_gcs_credentials(session: AsyncSession):
        """Return GCS credentials capable of v4 signing.

        Routes through credential_injector so vm_default installs get
        impersonated bootstrap credentials -- which sign via the IAM
        SignBlob API (token-creator includes signBlob on the target SA).
        Legacy service_account_key installs get a service_account.Credentials
        that signs locally with its private key. Either way, the returned
        credentials work for blob.generate_signed_url(version="v4").
        """
        from app.adapters.credentials import credential_injector
        from app.platform.platform_config_service import PlatformConfigService

        config = await PlatformConfigService.get_many(
            session,
            [
                "gcp_credential_source",
                "gcp_service_account_key",
                "gcp_service_account_email",
                "gcp_bootstrap_sa_email",
            ],
        )

        try:
            return credential_injector.load_gcp_credentials(config)
        except Exception as e:
            logger.warning("Failed to load GCS credentials from platform_config: %s", e)
            return None

    @staticmethod
    async def _get_ingest_bucket(session: AsyncSession) -> str:
        """Read ingest bucket name from platform_config."""
        from app.platform.platform_config_service import PlatformConfigService

        name = await PlatformConfigService.get(session, "ingest_bucket_name")
        if not name or name == "null":
            raise ValidationError("Ingest bucket not configured. Deploy storage infrastructure first.")
        return name

    @staticmethod
    async def initiate_upload(session: AsyncSession, *args, **kwargs) -> dict:
        """Initiate a direct upload as an admitted storage writer (see ``_initiate_admitted``).

        Registered before the pause is read, so it is either refused or waited for by destroy."""
        from app.services.storage_write_admission import admitted

        async with admitted(session):
            return await UploadService._initiate_admitted(session, *args, **kwargs)

    @staticmethod
    async def _initiate_admitted(
        session: AsyncSession,
        org_id: int,
        user_id: int,
        filename: str,
        expected_size: int | None,
        expected_md5: str | None,
        experiment_id: int | None,
        sample_ids: list[int] | None = None,
        project_id: int | None = None,
        is_global: bool = False,
        external_id: str | None = None,
        origin: str | None = None,
    ) -> dict:
        """Initiate an upload, returning upload_id and the provider session the browser sends to.

        The upload's checkpoint is recorded before the session exists, so the upload is tracked
        from the moment bytes may arrive, and survives a restart. The session is one bioAF can
        inspect and cancel with the provider (GCS resumable session or S3 multipart upload). ``external_id`` (optional) is
        recorded with it and used at completion; completion cannot substitute another. An
        identifier already used by a file of this organization is refused now (and again by the
        unique index at completion, for a race)."""
        from app.exceptions import ConflictError
        from app.services.file_service import validate_external_id
        from app.services.storage_write_admission import require_store_usable

        from app.services.file_organization import validate_object_filename

        filename = validate_object_filename(filename)
        external_id = validate_external_id(external_id)
        await verify_association_targets(
            session, org_id, project_id=project_id, experiment_id=experiment_id, sample_ids=sample_ids or []
        )
        if external_id is not None:
            taken = await session.scalar(
                select(File.id).where(File.organization_id == org_id, File.external_id == external_id)
            )
            if taken is not None:
                raise ConflictError("external_id_already_exists", plain_body=True)
        upload_id = str(uuid.uuid4())
        bucket_name = await UploadService._get_ingest_bucket(session)
        await require_store_usable(session, "ingest")
        gcs_path = f"uploads/{upload_id}/{filename}"

        from app.adapters.registry import get_storage_adapter

        adapter = get_storage_adapter()
        gcs_uri = adapter.build_uri(bucket_name, gcs_path)
        await _new_checkpoint(
            id=upload_id,
            organization_id=org_id,
            user_id=user_id,
            kind="session",
            stage="issued",
            filename=filename,
            source_uri=gcs_uri,
            file_uuid=uuid.uuid4(),
            expected_size=expected_size,
            expected_md5=expected_md5,
            project_id=project_id,
            experiment_id=experiment_id,
            sample_ids=sample_ids or [],
            is_global=is_global,
            external_id=external_id,
            progress_at=datetime.now(timezone.utc),
        )
        try:
            grant = await adapter.begin_upload_session(
                gcs_uri, content_type="application/octet-stream", size_bytes=expected_size, origin=origin
            )
        except Exception:
            # No session exists, so nothing can arrive: the upload settles as not sent.
            await _update_checkpoint(upload_id, stage="failed", last_error_category="signing_failed")
            raise
        from app.services.upload_settlement import first_check

        await _update_checkpoint(
            upload_id,
            session_protocol=grant.protocol,
            session_handle=grant.handle,
            next_reconcile_at=first_check(),
        )
        return {
            "upload_id": upload_id,
            # A GCS session also accepts one whole-file PUT, as the presigned URL did.
            "signed_url": grant.url,
            "gcs_uri": gcs_uri,
            "upload": {"protocol": grant.protocol, "url": grant.url, "part_size": grant.part_size},
        }

    @staticmethod
    async def sign_parts(session: AsyncSession, org_id: int, upload_id: str, part_numbers: list[int]) -> dict[int, str]:
        """Signed URLs for parts of an issued S3 multipart session."""
        from app.adapters.registry import get_storage_adapter

        checkpoint = await _load_checkpoint(session, org_id, upload_id)
        if checkpoint is None or checkpoint.kind != "session" or checkpoint.stage != "issued":
            raise StateError("Only an upload that is still being sent can get part links.")
        if checkpoint.session_protocol != "s3_multipart" or not checkpoint.session_handle:
            raise StateError("This upload is not sent in parts.")
        return await get_storage_adapter().sign_upload_parts(
            checkpoint.source_uri, checkpoint.session_handle, part_numbers
        )

    @staticmethod
    async def _assemble_once(adapter, upload_id: str, checkpoint: UploadCheckpoint, parts):
        """Assemble an S3 multipart upload, never twice: a completed upload ID no longer exists, so
        once assembly was requested, the upload's actual state is checked before asking again.
        Returns the provider's status afterwards."""
        from app.adapters.models import UploadSessionState

        uri, handle = checkpoint.source_uri, checkpoint.session_handle
        if checkpoint.assembly_requested_at is not None:
            status = await adapter.inspect_upload_session(uri, handle)
            if status.state is not UploadSessionState.ACTIVE:
                # Completed (proceed), ended (refused below) or no answer (the caller checks again).
                if status.state is UploadSessionState.ENDED:
                    raise ValidationError("Invalid or expired upload_id")
                return status
        if not parts:
            raise ValidationError("The uploaded parts are required to complete this upload.")
        await _update_checkpoint(upload_id, assembly_requested_at=datetime.now(timezone.utc))
        try:
            await adapter.finish_upload_session(uri, handle, parts)
        except Exception:
            # The answer may be lost after the provider assembled it: decide from its actual state.
            status = await adapter.inspect_upload_session(uri, handle)
            if status.state in (UploadSessionState.COMPLETED, UploadSessionState.UNKNOWN):
                return status
            raise
        return await adapter.inspect_upload_session(uri, handle)

    @staticmethod
    def _data_uploaded_event(
        *,
        org_id: int,
        user_id: int,
        file_id: int,
        filename: str,
        file_type: str,
        experiment_id: int | None,
    ) -> dict:
        """Build the DATA_UPLOADED event payload. When the file was uploaded into
        an experiment, the experiment id rides along in metadata so the resulting
        notification can deep-link to that experiment's Files tab; a standalone
        upload carries none and falls back to the Data & Files page."""
        metadata: dict = {}
        if experiment_id is not None:
            metadata["experiment_id"] = experiment_id
        return {
            "event_type": DATA_UPLOADED,
            "org_id": org_id,
            "user_id": user_id,
            "entity_type": "file",
            "entity_id": file_id,
            "title": f"File uploaded: {filename}",
            "message": f"File '{filename}' ({file_type}) uploaded successfully",
            "summary": f"File '{filename}' uploaded",
            "metadata": metadata,
        }

    @staticmethod
    async def complete_upload(
        session: AsyncSession,
        org_id: int,
        upload_id: str,
        actual_md5: str,
        parts=None,
    ) -> File:
        """Complete a direct upload: assemble it (S3 parts), confirm the provider has the whole
        object, verify the MD5 assertion, then file the received bytes.

        A repeated completion of a filed upload returns its File. A cancelled upload is never
        filed. If filing fails the bytes stay tracked and UploadReceivedNotFiled is raised (bioAF
        retries automatically)."""
        from app.adapters.models import UploadSessionState
        from app.adapters.registry import get_storage_adapter
        from app.services.file_verification_service import agreed_upload_md5

        await lock_upload(session, upload_id)
        checkpoint = await _load_checkpoint(session, org_id, upload_id)
        if checkpoint is None or checkpoint.stage in ("cancelling", "cancelled", "failed", "transferring"):
            raise ValidationError("Invalid or expired upload_id")
        if checkpoint.stage == "filed" and checkpoint.file_id is not None:
            existing = await FileService.get_file(session, checkpoint.file_id, org_id)
            if existing is not None:
                return existing
        # Both MD5s are client assertions: they must agree, and the agreed value is then verified
        # independently against the stored bytes. Neither is recorded as an observed checksum.
        asserted_md5 = agreed_upload_md5(checkpoint.expected_md5, actual_md5)
        if checkpoint.stage == "issued" and checkpoint.kind == "session":
            adapter = get_storage_adapter()
            uri, handle = checkpoint.source_uri, checkpoint.session_handle
            if checkpoint.session_protocol == "s3_multipart":
                status = await UploadService._assemble_once(adapter, upload_id, checkpoint, parts)
            else:
                status = await adapter.inspect_upload_session(uri, handle)
            if status.state is not UploadSessionState.COMPLETED:
                raise UploadNotFinished("The upload has not finished sending. Try again in a moment.")
        if checkpoint.stage == "issued":
            await _update_checkpoint(upload_id, stage="received", asserted_md5=asserted_md5, next_reconcile_at=None)
        return await UploadService._file_or_wait(session, upload_id)

    @staticmethod
    async def simple_upload(session: AsyncSession, *args, **kwargs) -> File:
        """A proxied upload as an admitted storage writer (see ``_simple_admitted``)."""
        from app.services.storage_write_admission import admitted

        async with admitted(session):
            return await UploadService._simple_admitted(session, *args, **kwargs)

    @staticmethod
    async def _simple_admitted(
        session: AsyncSession,
        org_id: int,
        user_id: int,
        filename: str,
        file_obj,
        size_bytes: int | None = None,
        file_type: str | None = None,
        project_id: int | None = None,
        experiment_id: int | None = None,
        sample_ids: list[int] | None = None,
        is_global: bool = False,
        external_id: str | None = None,
    ) -> File:
        """Stream a file directly to storage without buffering the full content, then file it.

        Builds the write URI via the adapter's backend-neutral resolve_uri so the
        proxied upload path works on any storage backend (GCS, NFS, ...), not just
        object stores (Phase 7). The checkpoint is recorded before the transfer; a filing failure
        keeps the received bytes tracked and raises UploadReceivedNotFiled.
        """
        from app.services.file_organization import validate_object_filename
        from app.services.storage_write_admission import require_store_usable, tracked_transfer

        filename = validate_object_filename(filename)
        # Before the storage write (admission already checked the pause): an unusable store or a
        # refused target leaves no object and no record.
        await verify_association_targets(
            session, org_id, project_id=project_id, experiment_id=experiment_id, sample_ids=sample_ids or []
        )
        await require_store_usable(session, "ingest")
        upload_id = str(uuid.uuid4())
        gcs_path = f"uploads/{upload_id}/{filename}"

        from app.adapters.models import StorageStore
        from app.adapters.registry import get_storage_adapter

        adapter = get_storage_adapter()
        try:
            gcs_uri = await adapter.resolve_uri(StorageStore.INGEST, gcs_path)
        except DomainError:
            raise
        except Exception as exc:
            raise upload_failure("transfer", exc) from None

        await _new_checkpoint(
            id=upload_id,
            organization_id=org_id,
            user_id=user_id,
            kind="proxied",
            stage="transferring",
            filename=filename,
            source_uri=gcs_uri,
            file_uuid=uuid.uuid4(),
            expected_size=size_bytes,
            project_id=project_id,
            experiment_id=experiment_id,
            sample_ids=sample_ids or [],
            is_global=is_global,
            external_id=external_id,
        )
        async with tracked_transfer(upload_id):
            try:
                await adapter.upload_file(gcs_uri, file_obj)
            except DomainError:
                await _update_checkpoint(upload_id, stage="failed", last_error_category="transfer_refused")
                raise
            except Exception as exc:
                await _update_checkpoint(upload_id, stage="failed", last_error_category="transfer_failed")
                raise upload_failure("transfer", exc) from None
            await _update_checkpoint(upload_id, stage="received", received_size=size_bytes)

        return await UploadService._file_or_wait(session, upload_id, file_type=file_type)

    # ------------------------------------------------------------------
    # Filing: one path for completion, automatic retry and Retry now
    # ------------------------------------------------------------------

    @staticmethod
    async def _file_or_wait(session: AsyncSession, upload_id: str, *, file_type: str | None = None) -> File:
        """File received bytes now, or, while writes are paused, keep them for the automatic retry
        (answered as received but not yet filed, like any deferred filing)."""
        from app.services.storage_write_admission import StorageWritesPaused

        try:
            return await UploadService._file_or_defer(session, upload_id, file_type=file_type)
        except StorageWritesPaused:
            checkpoint = await session.get(UploadCheckpoint, upload_id, populate_existing=True)
            await _update_checkpoint(upload_id, next_attempt_at=datetime.now(timezone.utc) + PAUSED_RETRY)
            experiment = None
            if checkpoint.experiment_id is not None:
                from app.models.experiment import Experiment

                experiment = await session.scalar(
                    select(Experiment.name).where(Experiment.id == checkpoint.experiment_id)
                )
            raise UploadReceivedNotFiled(
                _pending_message(checkpoint.reference, checkpoint.attempts, experiment),
                details={
                    "upload_id": upload_id,
                    "reference": checkpoint.reference,
                    "experiment_id": checkpoint.experiment_id,
                },
            ) from None

    @staticmethod
    async def _file_or_defer(session: AsyncSession, upload_id: str, *, file_type: str | None = None) -> File:
        """File a received upload on the caller's session, or record the failure and defer.

        The filing runs in a savepoint: a failure leaves the caller's session usable and no File,
        link or event behind, while the checkpoint (committed separately) keeps the bytes tracked.
        Domain errors (a target that no longer exists or is not permitted) propagate unchanged.
        """
        checkpoint = await session.get(UploadCheckpoint, upload_id, populate_existing=True)
        if checkpoint is None:
            raise ValidationError("Invalid or expired upload_id")
        # Read before the attempt: a failed savepoint expires the object.
        reference, experiment_id = checkpoint.reference, checkpoint.experiment_id
        # Completion, Retry now and the automatic retry may overlap: they take turns on a lock held
        # until the caller's transaction ends, and a later attempt then sees the filed checkpoint
        # and returns its File. An advisory lock, not a row lock, so the attempt can still record
        # its planned destination and a failure in separate transactions.
        await lock_upload(session, upload_id)
        checkpoint = await session.get(UploadCheckpoint, upload_id, populate_existing=True)
        if checkpoint is None or checkpoint.stage not in ("received", "filed"):
            raise StateError("Only a received upload that has not been filed can be filed.")
        if checkpoint.file_uuid is None and checkpoint.stage == "received":
            # Recorded before reservations existed: reserve its identity durably, under the lock,
            # before its first attempt, so every later attempt registers the same File.
            await _update_checkpoint(upload_id, file_uuid=uuid.uuid4())
            checkpoint = await session.get(UploadCheckpoint, upload_id, populate_existing=True)
        from app.services.storage_write_admission import STORAGE_PAUSED, StorageWritesPaused, admitted

        try:
            # Filing copies bytes between stores: an admitted writer, refused while writes are paused.
            async with admitted(session, error=StorageWritesPaused, message=STORAGE_PAUSED):
                staged = None
                if checkpoint.stage == "received" and checkpoint.experiment_id:
                    # Before the savepoint: the copy's observations and receipt are committed on
                    # their own while this session's checkpoint stays clean (see _organized_copy).
                    staged = await UploadService._organized_copy(session, checkpoint)
                async with session.begin_nested():
                    return await UploadService._file_checkpoint(session, checkpoint, file_type=file_type, staged=staged)
        except DomainError:
            raise
        except Exception as exc:
            attempts = await _record_filing_failure(upload_id, exc)
            experiment = None
            if experiment_id is not None:
                from app.models.experiment import Experiment

                experiment = await session.scalar(select(Experiment.name).where(Experiment.id == experiment_id))
            raise UploadReceivedNotFiled(
                _pending_message(reference, attempts, experiment),
                details={"upload_id": upload_id, "reference": reference, "experiment_id": experiment_id},
            ) from None

    @staticmethod
    def _owner_metadata(checkpoint: UploadCheckpoint) -> dict[str, str]:
        """The server-owned tags an organized copy of this upload carries (never client values)."""
        return {
            "bioaf-file-uuid": str(checkpoint.file_uuid),
            "bioaf-organization-id": str(checkpoint.organization_id),
            "bioaf-upload-id": checkpoint.id,
        }

    @staticmethod
    def _is_owned_copy(observation, checkpoint: UploadCheckpoint) -> bool:
        """The recovery predicate: an object at the reserved destination is this upload's copy only if
        its server-owned tags, its size, any recorded destination version and any comparable trusted
        checksums all agree. A path, a name or a size alone never establishes ownership."""
        tags = observation.metadata or {}
        identity = observation.identity
        if any(tags.get(k) != v for k, v in UploadService._owner_metadata(checkpoint).items()):
            return False
        if checkpoint.source_observed_size is not None and identity.size_bytes != checkpoint.source_observed_size:
            return False
        if checkpoint.destination_version is not None and identity.version != checkpoint.destination_version:
            return False
        if checkpoint.source_native_md5 and identity.native_md5 and identity.native_md5 != checkpoint.source_native_md5:
            return False
        return True

    @staticmethod
    def _is_legacy_plan(checkpoint: UploadCheckpoint) -> bool:
        planned = checkpoint.planned_destination
        return planned is not None and f"/files/{checkpoint.file_uuid}/" not in planned

    @staticmethod
    async def _organized_copy(session: AsyncSession, checkpoint: UploadCheckpoint):
        """Copy received bytes to their reserved organized destination, create-only, and return
        ``(pinned source or None, destination identity)``. The caller holds the upload lock.

        Every observation (the exact destination, the source's version and size, the destination's
        version) is committed in the checkpoint's own transaction before the step that relies on it,
        and the caller's checkpoint is refreshed from that committed state rather than assigned, so
        no flush in the caller's transaction holds the row another progress write needs. Nothing is
        deleted here: the source is removed only after the File that replaces it is committed.
        """
        from app.adapters.models import DestinationExists, ObjectChanged, ObjectIdentity, StorageObjectNotFound
        from app.adapters.registry import get_storage_adapter
        from app.services.file_organization import FileOrganizationService

        adapter = get_storage_adapter()
        planned = checkpoint.planned_destination
        if UploadService._is_legacy_plan(checkpoint):
            # Planned by an earlier release at a name-only path that another upload may share. With the
            # source still here, file it under its own identity instead; without it, the bytes at that
            # path cannot be attributed to this upload, so the old evidence is kept and not adopted.
            if not await adapter.exists(checkpoint.source_uri):
                raise _FilingConflict("legacy_destination")
            planned = None
        if planned is None:
            planned = await FileOrganizationService.planned_location(
                session,
                checkpoint.source_uri,
                file_uuid=checkpoint.file_uuid,
                experiment_id=checkpoint.experiment_id,
                filename=checkpoint.filename,
            )
        try:
            source: ObjectIdentity | None = await adapter.object_identity(checkpoint.source_uri)
        except StorageObjectNotFound:
            source = None
        updates: dict = {}
        if planned != checkpoint.planned_destination:
            updates["planned_destination"] = planned
        if source is not None:
            if checkpoint.source_version is None:
                updates.update(
                    source_version=source.version,
                    source_observed_size=source.size_bytes,
                    source_native_md5=source.native_md5,
                )
            elif checkpoint.source_version != source.version:
                raise _FilingConflict("source_changed")
        if updates:
            await _update_checkpoint(checkpoint.id, **updates)
            await session.refresh(checkpoint)
        pinned = (
            ObjectIdentity(
                uri=checkpoint.source_uri,
                size_bytes=checkpoint.source_observed_size or 0,
                version=checkpoint.source_version,
                native_md5=checkpoint.source_native_md5,
            )
            if source is not None and checkpoint.source_version
            else None
        )
        for _attempt in range(2):
            try:
                observed = await adapter.inspect_object(planned)
            except StorageObjectNotFound:
                observed = None
            if observed is None:
                if pinned is None:
                    await _update_checkpoint(checkpoint.id, stage="failed", last_error_category="bytes_missing")
                    raise StateError("The received copy of this upload is no longer stored.")
                try:
                    destination = await adapter.copy_create_only(
                        checkpoint.source_uri,
                        planned,
                        source_identity=pinned,
                        metadata=UploadService._owner_metadata(checkpoint),
                    )
                except DestinationExists:
                    continue  # created meanwhile: decide from what is actually there
                except ObjectChanged:
                    raise _FilingConflict("source_changed") from None
            elif UploadService._is_owned_copy(observed, checkpoint):
                destination = observed.identity  # an earlier attempt's copy (a lost response, a crash)
            else:
                raise _FilingConflict("destination_conflict")
            if checkpoint.destination_version != destination.version:
                await _update_checkpoint(checkpoint.id, destination_version=destination.version)
                await session.refresh(checkpoint)
            return pinned, destination
        raise _FilingConflict("destination_conflict")

    @staticmethod
    async def _file_checkpoint(
        session: AsyncSession, checkpoint: UploadCheckpoint, *, file_type: str | None = None, staged=None
    ) -> File:
        """Register the received bytes as a File, link them, and start readiness.

        ``staged`` is ``(source, destination)`` from ``_organized_copy`` for an upload into an
        experiment: the File is registered at the destination with the source recorded for removal
        once this transaction commits. Without an experiment the File stays at its unique intake URI.
        Idempotent across retries: a filed checkpoint returns its File.
        """
        from app.services.file_verification_service import Expectation, FileVerificationService, agreed_upload_md5

        org_id = checkpoint.organization_id
        if checkpoint.stage == "filed" and checkpoint.file_id is not None:
            existing = await FileService.get_file(session, checkpoint.file_id, org_id)
            if existing is not None:
                return existing
        # Checked when the upload started; checked again because the targets may have changed since.
        await verify_association_targets(
            session,
            org_id,
            project_id=checkpoint.project_id,
            experiment_id=checkpoint.experiment_id,
            sample_ids=list(checkpoint.sample_ids or []),
        )

        storage_uri = checkpoint.source_uri
        experiment_id = checkpoint.experiment_id
        source = destination = None
        if experiment_id:
            if staged is None:
                raise StateError("An upload into an experiment is filed only after its organized copy.")
            source, destination = staged
            storage_uri = destination.uri

        filename = checkpoint.filename
        file_type = file_type or UploadService._detect_file_type(filename)
        illumina_info = UploadService.parse_illumina_filename(filename)
        tags: list[str] = []
        if illumina_info:
            tags.append(f"lane:{illumina_info['lane']}")
            tags.append(f"read:{illumina_info['read']}")
            tags.append(f"sample:{illumina_info['sample_name']}")

        user_id = checkpoint.user_id
        file = await FileService.create_file_record(
            session,
            org_id=org_id,
            user_id=user_id,
            filename=filename,
            storage_uri=storage_uri,
            size_bytes=checkpoint.expected_size if checkpoint.expected_size is not None else checkpoint.received_size,
            md5_checksum=None,
            file_type=file_type,
            tags=tags,
            project_id=checkpoint.project_id,
            experiment_id=experiment_id,
            is_global=checkpoint.is_global,
            external_id=checkpoint.external_id,
            file_uuid=checkpoint.file_uuid,
        )

        # Sequencing identity as typed columns. The tags above are kept for one
        # release so anything still reading them keeps working, but these are
        # what the sample sheet pairs mates on: a lane held as a string is why
        # `lane:1` here and `lane:001` from another ingest path used to describe
        # one physical lane as two units. A name outside the convention leaves
        # them NULL, never a fabricated value.
        # A lane is 1-based, and `R[12I]` also matches `RI`, which is not a read
        # code that exists. Both are filtered here rather than written and
        # explained later: a typed column that holds a fiction is worse than one
        # holding NULL.
        if illumina_info:
            file.lane = illumina_info["lane"] or None
            file.read_type = illumina_info["read"] if illumina_info["read"] in READ_TYPES else None

        # Then the file's own header, which carries the flow cell and lane
        # whatever the name says, and which is the only source for a lab that
        # follows no naming convention at all. The header WINS over the name
        # above: a name is a hint, and a file renamed after ingest loses it.
        # Optional throughout, so a file bioAF cannot read leaves the columns as
        # they are and the sheet treats everything unknown as one unit.
        await enrich_from_header(file)

        for sample_id in checkpoint.sample_ids or []:
            await FileService.link_file_to_sample(session, file.id, sample_id)

        if experiment_id:
            from app.models.experiment import Experiment
            from app.services.audit_service import log_action
            from app.services.storage_cleanup import cleanup_slot

            # The experiment's project is denormalized onto the file, as assignment always did.
            exp_project_id = await session.scalar(
                select(Experiment.project_id).where(
                    Experiment.id == experiment_id, Experiment.organization_id == org_id
                )
            )
            if exp_project_id is not None:
                file.project_id = exp_project_id
            # The ingest copy is removed only after this transaction commits (storage_cleanup).
            if source is not None and source.uri != storage_uri:
                file.pending_storage_cleanup = cleanup_slot(source)
            await log_action(
                session,
                user_id=user_id,
                entity_type="file",
                entity_id=file.id,
                action="assigned_to_experiment",
                details={"experiment_id": experiment_id, "old_uri": checkpoint.source_uri, "new_uri": storage_uri},
            )

        # Announce the registration with its final context and links, then start readiness: an
        # asserted MD5 is verified against the stored bytes; without one, existence and size suffice.
        # Flush pending attribute changes, then reload what file organization rewrote with SQL.
        await session.flush()
        await session.refresh(file)
        await FileService.finalize_registration(session, file)
        # The completion's assertion, or (when nobody reported completion and reconciliation found
        # the upload finished) the checksum declared when the upload started.
        asserted_md5 = checkpoint.asserted_md5 or agreed_upload_md5(checkpoint.expected_md5, None)
        await FileVerificationService.begin(
            session,
            file,
            Expectation.md5("upload_assertion", asserted_md5) if asserted_md5 else Expectation.none("upload"),
            expected_size=checkpoint.expected_size
            if checkpoint.expected_size is not None
            else checkpoint.received_size,
        )

        if experiment_id and file_type == "fastq" and user_id is not None:
            await UploadService._auto_update_experiment_status(session, experiment_id, org_id, user_id)

        # Filed with the File, in the same transaction: both commit or neither does.
        checkpoint.stage = "filed"
        checkpoint.file_id = file.id
        checkpoint.next_attempt_at = None
        await session.flush()

        # Announced once the caller commits the file; dropped if it never does.
        defer_event(
            session,
            DATA_UPLOADED,
            UploadService._data_uploaded_event(
                org_id=org_id,
                user_id=user_id or 0,
                file_id=file.id,
                filename=filename,
                file_type=file_type,
                experiment_id=experiment_id,
            ),
        )
        return file

    @staticmethod
    async def retry_upload(session: AsyncSession, org_id: int, upload_id: str) -> File:
        """Retry filing now (Retry now). Raises UploadReceivedNotFiled if it fails again."""
        checkpoint = await _load_checkpoint(session, org_id, upload_id)
        if checkpoint is None:
            from app.exceptions import NotFoundError

            raise NotFoundError("Upload not found")
        if checkpoint.stage == "filed" and checkpoint.file_id is not None:
            existing = await FileService.get_file(session, checkpoint.file_id, org_id)
            if existing is not None:
                return existing
        if checkpoint.stage != "received":
            raise StateError("Only a received upload that has not been filed can be retried.")
        return await UploadService._file_or_defer(session, upload_id)

    @staticmethod
    async def cancel_upload(
        session: AsyncSession, org_id: int, upload_id: str, user_id: int, *, reason: str = "cancelled"
    ) -> str:
        """Cancel an unfiled upload; returns its stage afterwards (``cancelling``).

        Always recorded as cancelling first, committed by the caller before anything is deleted, so
        a crash can never leave a received upload eligible for filing with its bytes half removed.
        Filing is refused from then on. ``upload_settlement.settle`` (run right after the caller
        commits, and in the background) finishes it: received bytes are removed from both places
        they may be (the ingest copy and any organized copy an interrupted filing published), only
        where they are provably this upload's; an upload still being sent keeps counting as a
        possible writer until the provider confirms nothing more can arrive.
        """
        from app.services.audit_service import log_action

        await lock_upload(session, upload_id)
        checkpoint = await _load_checkpoint(session, org_id, upload_id)
        if checkpoint is None:
            from app.exceptions import NotFoundError

            raise NotFoundError("Upload not found")
        if checkpoint.stage in ("filed", "cancelled"):
            raise StateError("This upload is already filed or cancelled.")
        if checkpoint.stage == "cancelling":
            return "cancelling"
        now = datetime.now(timezone.utc)
        if checkpoint.stage != "issued":
            # The bytes are stored (or being stored by this server): settling removes bioAF's own
            # copies, with no provider to wait for.
            checkpoint.cancel_from_stage = checkpoint.stage
        checkpoint.stage = "cancelling"
        # "interrupted": the browser stopped it after a failure, not the uploader's choice.
        checkpoint.last_error_category = reason
        checkpoint.cancel_requested_at = now
        checkpoint.cancel_requested_by_user_id = user_id
        checkpoint.next_reconcile_at = now
        checkpoint.reconcile_attempts = 0
        checkpoint.next_attempt_at = None
        await log_action(
            session,
            user_id=user_id,
            entity_type="upload",
            entity_id=0,
            action="upload_cancelled",
            details={"upload_id": upload_id, "reference": checkpoint.reference, "filename": checkpoint.filename},
        )
        await session.flush()
        return checkpoint.stage

    @staticmethod
    async def retry_due(session_factory, *, limit: int = 20) -> int:
        """Run the automatic filing retries that are due. Returns how many were filed."""
        from app.services.storage_write_admission import StorageWritesPaused

        now = datetime.now(timezone.utc)
        async with session_factory() as session:
            due = (
                await session.execute(
                    select(UploadCheckpoint.id, UploadCheckpoint.organization_id)
                    .where(
                        UploadCheckpoint.stage == "received",
                        UploadCheckpoint.next_attempt_at.is_not(None),
                        UploadCheckpoint.next_attempt_at <= now,
                    )
                    .order_by(UploadCheckpoint.next_attempt_at)
                    .limit(limit)
                )
            ).all()
        filed = 0
        for upload_id, org_id in due:
            async with session_factory() as session:
                try:
                    file = await UploadService._file_or_defer(session, upload_id)
                    file_id = file.id
                    await session.commit()
                    filed += 1
                    from app.services.storage_cleanup import cleanup_after_commit

                    try:
                        await cleanup_after_commit(file_id, org_id, session_factory)
                    except Exception as exc:  # the periodic cleanup pass retries
                        logger.warning("Ingest copy cleanup deferred (error_type=%s)", type(exc).__name__)
                except UploadReceivedNotFiled:
                    await session.rollback()
                except StorageWritesPaused:
                    # Writes are paused: try again later without counting a failed attempt.
                    await session.rollback()
                    await _update_checkpoint(upload_id, next_attempt_at=datetime.now(timezone.utc) + PAUSED_RETRY)
                except DomainError as exc:
                    await session.rollback()
                    await _update_checkpoint(upload_id, next_attempt_at=None, last_error_category=exc.code[:48])
        return filed

    @staticmethod
    async def _auto_update_experiment_status(
        session: AsyncSession, experiment_id: int, org_id: int, user_id: int
    ) -> None:
        """Auto-transition experiment to fastq_uploaded if appropriate.

        Walks through intermediate statuses (registered -> library_prep ->
        sequencing -> fastq_uploaded) because uploading FASTQs implies the
        earlier steps already happened externally.
        """
        from app.services.experiment_service import ExperimentService

        # Statuses that precede fastq_uploaded, in order
        path_to_fastq = ["library_prep", "sequencing", "fastq_uploaded"]
        exp = await ExperimentService.get_experiment(session, experiment_id, org_id)
        if not exp or exp.status not in ("registered", "library_prep", "sequencing"):
            return

        try:
            # Find where we are in the path and advance from there
            current = exp.status
            for target in path_to_fastq:
                if current == "fastq_uploaded":
                    break
                await ExperimentService.update_status(session, experiment_id, org_id, user_id, target)
                current = target
        except Exception as e:
            logger.warning("Could not auto-update experiment status: %s", e)

    @staticmethod
    def _detect_file_type(filename: str) -> str:
        lower = filename.lower()
        if lower.endswith((".fastq.gz", ".fq.gz")):
            return "fastq"
        if lower.endswith(".bam"):
            return "bam"
        if lower.endswith(".h5ad"):
            return "h5ad"
        if lower.endswith(".pdf"):
            return "pdf"
        if lower.endswith(".png"):
            return "png"
        if lower.endswith(".svg"):
            return "svg"
        if lower.endswith(".csv"):
            return "csv"
        return "other"


async def reconcile_transfers_on_startup(session: AsyncSession) -> dict[str, int]:
    """Settle proxied transfers a restart cut off: kept as received when the bytes are stored,
    otherwise recorded as not sent. The caller commits."""
    from app.adapters.registry import get_storage_adapter

    adapter = get_storage_adapter()
    counts = {"received": 0, "failed": 0}
    rows = (
        (await session.execute(select(UploadCheckpoint).where(UploadCheckpoint.stage == "transferring")))
        .scalars()
        .all()
    )
    for checkpoint in rows:
        try:
            stored = await adapter.exists(checkpoint.source_uri)
        except Exception as exc:
            logger.warning("Could not check an interrupted upload (error_type=%s)", type(exc).__name__)
            continue
        if stored:
            checkpoint.stage = "received"
            checkpoint.next_attempt_at = datetime.now(timezone.utc)
            counts["received"] += 1
        else:
            checkpoint.stage = "failed"
            checkpoint.last_error_category = "interrupted"
            counts["failed"] += 1
    await session.flush()
    return counts
