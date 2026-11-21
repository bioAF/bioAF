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

# Signed upload URLs are valid this long. Expiry is not proof the upload finished.
SIGNED_URL_SECONDS = 3600
# Automatic filing retries after the first failure, then the upload waits for Retry now / Cancel.
RETRY_DELAYS = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15))
MAX_AUTOMATIC_ATTEMPTS = len(RETRY_DELAYS)

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


async def _record_filing_failure(upload_id: str, exc: BaseException) -> int:
    """Count a failed filing attempt and schedule the next automatic one; return the attempt count."""
    async with _session_factory()() as s:
        checkpoint = await s.get(UploadCheckpoint, upload_id, with_for_update=True)
        if checkpoint is None:
            return 0
        checkpoint.attempts = (checkpoint.attempts or 0) + 1
        due = checkpoint.attempts <= MAX_AUTOMATIC_ATTEMPTS
        checkpoint.next_attempt_at = datetime.now(timezone.utc) + RETRY_DELAYS[checkpoint.attempts - 1] if due else None
        checkpoint.last_error_category = "filing_failed"
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
    async def initiate_upload(
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
    ) -> dict:
        """Initiate an upload, returning upload_id and signed URL.

        The upload's checkpoint is recorded before the URL is issued, so the upload is tracked
        from the moment bytes may arrive, and survives a restart. ``external_id`` (optional) is
        recorded with it and used at completion; completion cannot substitute another. An
        identifier already used by a file of this organization is refused now (and again by the
        unique index at completion, for a race)."""
        from app.exceptions import ConflictError
        from app.services.file_service import validate_external_id
        from app.services.storage_write_admission import require_store_usable, require_writes_open

        await require_writes_open(session)
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
            kind="signed",
            stage="issued",
            filename=filename,
            source_uri=gcs_uri,
            expected_size=expected_size,
            expected_md5=expected_md5,
            project_id=project_id,
            experiment_id=experiment_id,
            sample_ids=sample_ids or [],
            is_global=is_global,
            external_id=external_id,
            grant_expires_at=datetime.now(timezone.utc) + timedelta(seconds=SIGNED_URL_SECONDS),
        )
        try:
            signed_url = await adapter.generate_signed_url(
                gcs_uri, method="PUT", expiry_seconds=SIGNED_URL_SECONDS, content_type="application/octet-stream"
            )
        except Exception:
            # No URL was issued, so nothing can arrive: the upload settles as not sent.
            await _update_checkpoint(upload_id, stage="failed", last_error_category="signing_failed")
            raise

        return {
            "upload_id": upload_id,
            "signed_url": signed_url,
            "gcs_uri": gcs_uri,
        }

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
    ) -> File:
        """Complete a signed upload: verify the MD5 assertion, then file the received bytes.

        A repeated completion of a filed upload returns its File. If filing fails the bytes stay
        tracked and UploadReceivedNotFiled is raised (bioAF retries automatically)."""
        from app.services.file_verification_service import agreed_upload_md5

        checkpoint = await _load_checkpoint(session, org_id, upload_id)
        if checkpoint is None or checkpoint.stage in ("cancelled", "failed", "transferring"):
            raise ValidationError("Invalid or expired upload_id")
        if checkpoint.stage == "filed" and checkpoint.file_id is not None:
            existing = await FileService.get_file(session, checkpoint.file_id, org_id)
            if existing is not None:
                return existing
        # Both MD5s are client assertions: they must agree, and the agreed value is then verified
        # independently against the stored bytes. Neither is recorded as an observed checksum.
        asserted_md5 = agreed_upload_md5(checkpoint.expected_md5, actual_md5)
        if checkpoint.stage == "issued":
            await _update_checkpoint(upload_id, stage="received", asserted_md5=asserted_md5)
        return await UploadService._file_or_defer(session, upload_id)

    @staticmethod
    async def simple_upload(
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
        from app.services.storage_write_admission import require_store_usable, require_writes_open, tracked_transfer

        # Before the storage write: paused storage, an unusable store or a refused target leaves no
        # object and no record.
        await require_writes_open(session)
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

        return await UploadService._file_or_defer(session, upload_id, file_type=file_type)

    # ------------------------------------------------------------------
    # Filing: one path for completion, automatic retry and Retry now
    # ------------------------------------------------------------------

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
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:ns, hashtext(:id))").bindparams(ns=_FILING_LOCK_NAMESPACE, id=upload_id)
        )
        checkpoint = await session.get(UploadCheckpoint, upload_id, populate_existing=True)
        if checkpoint is None or checkpoint.stage not in ("received", "filed"):
            raise StateError("Only a received upload that has not been filed can be filed.")
        try:
            async with session.begin_nested():
                return await UploadService._file_checkpoint(session, checkpoint, file_type=file_type)
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
    async def _file_checkpoint(
        session: AsyncSession, checkpoint: UploadCheckpoint, *, file_type: str | None = None
    ) -> File:
        """Register the received bytes as a File, link and move them, and start readiness.

        Idempotent across retries: a filed checkpoint returns its File, and bytes a previous attempt
        already moved are found at their planned destination, never copied twice.
        """
        from app.adapters.registry import get_storage_adapter
        from app.services.file_verification_service import Expectation, FileVerificationService

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

        adapter = get_storage_adapter()
        storage_uri = checkpoint.source_uri
        experiment_id = checkpoint.experiment_id
        if experiment_id:
            from app.services.file_organization import FileOrganizationService

            planned = await FileOrganizationService.planned_location(
                session, checkpoint.source_uri, experiment_id, checkpoint.filename
            )
            if checkpoint.planned_destination != planned:
                await _update_checkpoint(checkpoint.id, planned_destination=planned)
                checkpoint.planned_destination = planned
            if planned != checkpoint.source_uri and not await adapter.exists(checkpoint.source_uri):
                # An earlier attempt moved the bytes and then failed: adopt them where they are.
                if not await adapter.exists(planned):
                    await _update_checkpoint(checkpoint.id, stage="failed", last_error_category="bytes_missing")
                    raise StateError("The received copy of this upload is no longer stored.")
                storage_uri = planned

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

        # Move file from ingest to raw bucket under experiment prefix
        if experiment_id:
            from app.services.file_organization import FileOrganizationService

            await FileOrganizationService.assign_file_to_experiment(
                session, file.id, experiment_id, user_id, commit=False
            )

        # Announce the registration with its final context and links, then start readiness: an
        # asserted MD5 is verified against the stored bytes; without one, existence and size suffice.
        # Flush pending attribute changes, then reload what file organization rewrote with SQL.
        await session.flush()
        await session.refresh(file)
        await FileService.finalize_registration(session, file)
        asserted_md5 = checkpoint.asserted_md5
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
    async def cancel_upload(session: AsyncSession, org_id: int, upload_id: str, user_id: int) -> None:
        """Remove an unfiled upload: delete its received copy (bioAF's own upload object) and stop tracking it.

        Cancelling an issued signed URL is an administrator's acknowledgment: a URL cannot be
        revoked, and anything it later writes lands under this upload's own path in ingest."""
        from app.adapters.registry import get_storage_adapter
        from app.services.audit_service import log_action

        checkpoint = await _load_checkpoint(session, org_id, upload_id)
        if checkpoint is None:
            from app.exceptions import NotFoundError

            raise NotFoundError("Upload not found")
        if checkpoint.stage in ("filed", "cancelled"):
            raise StateError("This upload is already filed or cancelled.")
        expected_prefix = f"uploads/{upload_id}/"
        adapter = get_storage_adapter()
        _bucket, key = adapter.parse_uri(checkpoint.source_uri)
        if key.startswith(expected_prefix):
            await adapter.delete(checkpoint.source_uri)
        await _update_checkpoint(upload_id, stage="cancelled", next_attempt_at=None)
        await log_action(
            session,
            user_id=user_id,
            entity_type="upload",
            entity_id=0,
            action="upload_cancelled",
            details={"upload_id": upload_id, "reference": checkpoint.reference, "filename": checkpoint.filename},
        )

    @staticmethod
    async def retry_due(session_factory, *, limit: int = 20) -> int:
        """Run the automatic filing retries that are due. Returns how many were filed."""
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
                    await UploadService._file_or_defer(session, upload_id)
                    await session.commit()
                    filed += 1
                except UploadReceivedNotFiled:
                    await session.rollback()
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
