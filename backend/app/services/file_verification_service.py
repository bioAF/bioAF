"""Checksum verification of stored files: expectations, persisted state, and the status vocabulary.

An **expectation** is a checksum a caller or source asserted (an upload's MD5, a fetchngs CSV value).
An **observation** is a digest bioAF obtained independently from the final stored object, either
from a trustworthy provider digest of that exact object version (``provider``) or by reading the
bytes (``streamed``). A file is ``verified`` only when an observation of its current object matches
its current expectation. Provider metadata alone is never an expectation, so a file without one is
not compared with itself.

Statuses (one current record per file, ``app/models/file_verification.py``):

- ``waiting_expectation``: a source that may carry a checksum (a fetchngs CSV) is not resolved yet.
- ``not_required``: the source was resolved and has no checksum; existence and size may authorize
  readiness.
- ``queued`` / ``preparing`` / ``verifying``: required work is waiting, preparing a protected NFS
  copy, or obtaining and comparing a digest.
- ``verified``: the observation matches the expectation for the current object.
- ``checksum_mismatch``: they differ; both are kept and the file stays unready.
- ``failed``: an operational error prevented verification; ``error_code`` and ``retryable`` say why.
- ``unverified_legacy``: (derived, never stored) a historical checksum with no evidence behind it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.storage.digest import normalize_md5  # noqa: F401 - re-exported
from app.exceptions import ValidationError
from app.models.file import File
from app.models.file_verification import FileVerification

if TYPE_CHECKING:
    from app.adapters.models import ObjectIdentity

WAITING_EXPECTATION = "waiting_expectation"
NOT_REQUIRED = "not_required"
QUEUED = "queued"
PREPARING = "preparing"
VERIFYING = "verifying"
VERIFIED = "verified"
CHECKSUM_MISMATCH = "checksum_mismatch"
FAILED = "failed"
UNVERIFIED_LEGACY = "unverified_legacy"

REQUIRED_STATES = frozenset({QUEUED, PREPARING, VERIFYING, VERIFIED, CHECKSUM_MISMATCH})


def agreed_upload_md5(expected: str | None, actual: str | None) -> str | None:
    """The MD5 an upload asserts: initiation's expectation and/or completion's value, normalized.

    Both are client assertions, not observations. When both are given they must agree (the existing
    upload rule); an empty completion value asserts nothing.
    """
    agreed = normalize_md5(expected) if expected else None
    claimed = normalize_md5(actual) if actual and actual.strip() else None
    if agreed and claimed and agreed != claimed:
        raise ValidationError(f"MD5 mismatch: expected {agreed}, got {claimed}")
    return agreed or claimed


@dataclass(frozen=True)
class Expectation:
    """What a registration path knows about a file's checksum when it registers it."""

    kind: str  # "none" | "md5" | "pending"
    source: str
    value: str | None = None
    context: dict = field(default_factory=dict)

    @classmethod
    def none(cls, source: str) -> "Expectation":
        """The source was resolved and carries no checksum."""
        return cls("none", source)

    @classmethod
    def md5(cls, source: str, value: str) -> "Expectation":
        return cls("md5", source, value=value)

    @classmethod
    def pending(cls, source: str, context: dict) -> "Expectation":
        """The source may carry a checksum but is not resolved yet."""
        return cls("pending", source, context=dict(context))


async def current_record(session: AsyncSession, file_id: int, *, lock: bool = False) -> FileVerification | None:
    query = select(FileVerification).where(FileVerification.file_id == file_id)
    if lock:
        query = query.with_for_update()
    return (await session.execute(query)).scalar_one_or_none()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _reset_work(record: FileVerification) -> None:
    record.observed_checksum = None
    record.observed_method = None
    record.observed_bytes = None
    record.observed_uri = None
    record.observed_object_version = None
    record.verified_at = None
    record.stage = None
    record.bytes_processed = None
    record.total_bytes = None
    record.attempt_count = 0
    record.lease_token = None
    record.lease_expires_at = None
    record.error_code = None
    record.retryable = None


async def records_for(session: AsyncSession, file_ids: list[int]) -> dict[int, FileVerification]:
    if not file_ids:
        return {}
    rows = (await session.execute(select(FileVerification).where(FileVerification.file_id.in_(file_ids)))).scalars()
    return {r.file_id: r for r in rows}


class FileVerificationService:
    @staticmethod
    async def record_expectation(
        session: AsyncSession,
        file: File,
        *,
        source: str,
        md5: str,
        context: dict | None = None,
    ) -> FileVerification:
        """Require ``md5`` to be verified against the file's final stored bytes.

        Runs in the caller's transaction. The same expectation again keeps the existing record and its
        evidence; a different one starts a new revision, discarding evidence for the old one (an
        in-flight result for the old revision can no longer be applied).
        """
        expected = normalize_md5(md5)
        record = await current_record(session, file.id, lock=True)
        if record is None:
            record = FileVerification(organization_id=file.organization_id, file_id=file.id, status=QUEUED)
            session.add(record)
        elif record.expected_checksum == expected and record.status in REQUIRED_STATES | {FAILED}:
            return record
        elif record.expected_checksum is not None:
            record.expectation_revision = (record.expectation_revision or 1) + 1
        _reset_work(record)
        record.status = QUEUED
        record.expectation_source = source
        record.expected_algorithm = "md5"
        record.expected_checksum = expected
        record.expectation_resolved = True
        if context is not None:
            record.discovery_context = context
        record.next_attempt_at = _now()
        await session.flush()
        return record

    @staticmethod
    async def record_not_required(session: AsyncSession, file: File, *, source: str) -> FileVerification:
        """The file's source is resolved and carries no checksum. Never waives an existing expectation."""
        record = await current_record(session, file.id, lock=True)
        if record is not None and record.expected_checksum is not None:
            return record
        if record is None:
            record = FileVerification(organization_id=file.organization_id, file_id=file.id, status=NOT_REQUIRED)
            session.add(record)
        _reset_work(record)
        record.status = NOT_REQUIRED
        record.expectation_source = source
        record.expectation_resolved = True
        record.next_attempt_at = None
        await session.flush()
        return record

    @staticmethod
    async def await_expectation(session: AsyncSession, file: File, *, source: str, context: dict) -> FileVerification:
        """A source that may carry a checksum is not resolved yet; keep the file from optional readiness.

        Only a file with no verification record starts discovery. Saying the same thing again (a
        repeated attach) never changes an existing record: discovery already waiting keeps its
        scheduled backoff and attempt budget, and a terminal outcome (a failed discovery, a
        mismatch, a proof) stays until an authorized retry or a resolved source replaces it.
        """
        record = await current_record(session, file.id, lock=True)
        if record is not None:
            return record
        record = FileVerification(
            organization_id=file.organization_id,
            file_id=file.id,
            status=WAITING_EXPECTATION,
            expectation_resolved=False,
        )
        session.add(record)
        record.expectation_source = source
        record.discovery_context = context
        record.next_attempt_at = _now()
        await session.flush()
        return record

    @staticmethod
    async def begin(
        session: AsyncSession, file: File, expectation: Expectation, *, expected_size: int | None = None
    ) -> str:
        """Start a registered file on its way to readiness, in the registering transaction.

        Returns ``ready``, ``already_ready``, ``pending`` (the worker or discovery continues),
        ``checksum_mismatch``, or ``failed``. Only a metadata read happens here: an explicit MD5 is
        compared at once only against a trustworthy native digest of the final object; anything
        else (no native digest, an NFS store needing a protected copy) is left queued for the worker.
        """
        from app.adapters.registry import get_storage_adapter
        from app.services.file_service import FileService

        if expectation.kind == "pending":
            await FileVerificationService.await_expectation(
                session, file, source=expectation.source, context=expectation.context
            )
            return "pending"
        if expectation.kind == "none":
            record = await FileVerificationService.record_not_required(session, file, source=expectation.source)
            if record.status != NOT_REQUIRED:
                return "pending"  # an earlier expectation still applies
            return await FileService.mark_ready(session, file.id, file.organization_id, expected_size=expected_size)

        record = await FileVerificationService.record_expectation(
            session, file, source=expectation.source, md5=expectation.value or ""
        )
        if record.status == VERIFIED:
            return await FileService.mark_ready(session, file.id, file.organization_id)
        if record.status != QUEUED:
            return record.status
        adapter = get_storage_adapter()
        if hasattr(adapter, "prepare_managed_copy"):
            return "pending"
        uri = file.storage_uri or file.gcs_uri
        try:
            identity = await adapter.object_identity(uri)
        except Exception:
            return "pending"  # the worker retries the read with its own schedule
        if not identity.native_md5:
            return "pending"
        record.observed_checksum = identity.native_md5
        record.observed_method = "provider"
        record.observed_bytes = identity.size_bytes
        record.observed_uri = uri
        record.observed_object_version = identity.version
        record.next_attempt_at = None
        if identity.native_md5 != record.expected_checksum:
            record.status = CHECKSUM_MISMATCH
            record.error_code = "checksum_mismatch"
            record.retryable = False
            await session.flush()
            await notify_failure(session, file, "checksum_mismatch")
            return CHECKSUM_MISMATCH
        record.status = VERIFIED
        record.verified_at = _now()
        await session.flush()
        return await FileService.mark_ready(session, file.id, file.organization_id, expected_size=expected_size)

    @staticmethod
    async def relocate(session: AsyncSession, file: File, destination: "ObjectIdentity") -> FileVerification | None:
        """The file's bytes now live at ``destination`` (a copy); make the record describe that object.

        Runs in the relocating transaction, which must already hold the record's lock (the verifier's
        order: verification, then File). Evidence for the old object never carries over: a new
        expectation revision invalidates any in-flight claim, and observations are cleared. A
        required expectation is re-established at the destination: compared at once against a
        trustworthy native digest when the provider has one, otherwise queued for the worker. A
        mismatch or failure stays as it is (a copy proves nothing about it); the File's historical
        ``ready_at`` and registration events are untouched.
        """
        record = await current_record(session, file.id, lock=True)
        if record is None or record.status in (CHECKSUM_MISMATCH, FAILED, NOT_REQUIRED, WAITING_EXPECTATION):
            if record is not None and record.status in (NOT_REQUIRED, WAITING_EXPECTATION):
                record.lease_token = None
                record.lease_expires_at = None
            return record
        expected = record.expected_checksum
        artifact_uuid, artifact_uri = record.artifact_uuid, record.artifact_uri
        record.expectation_revision = (record.expectation_revision or 1) + 1
        _reset_work(record)
        record.artifact_uuid, record.artifact_uri = artifact_uuid, artifact_uri
        if expected is not None and destination.native_md5:
            record.observed_checksum = destination.native_md5
            record.observed_method = "provider"
            record.observed_bytes = destination.size_bytes
            record.observed_uri = destination.uri
            record.observed_object_version = destination.version
            record.next_attempt_at = None
            if destination.native_md5 == expected:
                record.status = VERIFIED
                record.verified_at = _now()
            else:
                record.status = CHECKSUM_MISMATCH
                record.error_code = "checksum_mismatch"
                record.retryable = False
                await session.flush()
                await notify_failure(session, file, "checksum_mismatch")
                return record
        else:
            record.status = QUEUED
            record.next_attempt_at = _now()
        await session.flush()
        return record

    @staticmethod
    async def retry(session: AsyncSession, file_id: int, org_id: int) -> FileVerification | None:
        """Requeue a failed or mismatched verification after correction (an authorized retry).

        None when the organization has no such record. Work already pending or verified is refused
        (StateError): a retry never resets a running attempt or redoes a proof. The attempt budget
        restarts; no registration event is produced.
        """
        from app.exceptions import StateError

        record = (
            await session.execute(
                select(FileVerification)
                .where(FileVerification.file_id == file_id, FileVerification.organization_id == org_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if record is None:
            return None
        if record.status not in (FAILED, CHECKSUM_MISMATCH):
            raise StateError("Only a failed or mismatched verification can be retried")
        if record.expectation_resolved and record.expected_checksum is None:
            # The checksum requirement was resolved as "none"; only the readiness read failed.
            # Repeat that read (never checksum discovery, which this source may not have).
            return await FileVerificationService._retry_readiness(session, record)
        artifact_uuid, artifact_uri = record.artifact_uuid, record.artifact_uri
        _reset_work(record)
        # Keep the previous protected copy recorded so the next attempt removes it first.
        record.artifact_uuid, record.artifact_uri = artifact_uuid, artifact_uri
        record.status = QUEUED if record.expected_checksum is not None else WAITING_EXPECTATION
        record.next_attempt_at = _now()
        await session.flush()
        return record

    @staticmethod
    async def _retry_readiness(session: AsyncSession, record: FileVerification) -> FileVerification:
        from app.exceptions import StateError
        from app.services.file_service import FileService, ReadinessRefused

        _reset_work(record)
        record.status = NOT_REQUIRED
        record.next_attempt_at = None
        await session.flush()
        try:
            await FileService.mark_ready(session, record.file_id, record.organization_id)
        except ReadinessRefused as exc:
            raise StateError(f"This file cannot be made ready ({exc})") from exc
        await session.refresh(record)
        return record

    @staticmethod
    def view(file: File, record: FileVerification | None) -> dict[str, Any] | None:
        """The ``verification`` object of file responses. Never starts any work."""
        if record is None:
            if file.md5_checksum:
                return {
                    "status": UNVERIFIED_LEGACY,
                    "algorithm": "md5",
                    "expected_checksum": None,
                    "observed_checksum": file.md5_checksum,
                    "method": None,
                    "verified_at": None,
                    "bytes_processed": None,
                    "total_bytes": None,
                    "error_code": None,
                    "retryable": None,
                }
            return None
        return {
            "status": record.status,
            "algorithm": record.expected_algorithm,
            "expected_checksum": record.expected_checksum,
            "observed_checksum": record.observed_checksum,
            "method": record.observed_method,
            "verified_at": record.verified_at,
            "bytes_processed": record.bytes_processed,
            "total_bytes": record.total_bytes,
            "error_code": record.error_code,
            "retryable": record.retryable,
        }


FAILURE_MESSAGES = {
    # The same words the File details panel shows (frontend: FileVerificationDetails.tsx REASONS).
    "checksum_mismatch": "The stored file’s checksum does not match the supplied checksum.",
    "storage_unavailable": "Storage could not be read to check the file.",
    "object_missing": "The stored file could not be found.",
    "object_changed": "The stored file changed while it was being checked.",
    "storage_permission_denied": "bioAF is not allowed to read the stored file.",
    "nfs_separation_unavailable": "Protected storage for checking files is not set up.",
    "nfs_capacity_insufficient": "There is not enough storage space to check the file.",
    "managed_copy_refused": "The file could not be copied safely for checking.",
    "verification_timeout": "Checking the file took too long.",
    "expectation_discovery_failed": "The supplied checksum could not be read.",
    "file_deleted": "The file was deleted before the check finished.",
}


async def notify_failure(session: AsyncSession, file: File, code: str) -> None:
    """Tell the file's responsible user (uploader, else the producing run's submitter) after commit.

    The message is plain and safe: a file name and a reason, never a URI, checksum, or raw error."""
    from app.services.event_types import FILE_VERIFICATION_FAILED
    from app.services.post_commit_events import defer_event

    user_id = file.uploader_user_id
    if user_id is None and file.source_pipeline_run_id is not None:
        from app.models.pipeline_run import PipelineRun

        run = await session.get(PipelineRun, file.source_pipeline_run_id)
        user_id = run.submitted_by_user_id if run is not None else None
    defer_event(
        session,
        FILE_VERIFICATION_FAILED,
        {
            "event_type": FILE_VERIFICATION_FAILED,
            "org_id": file.organization_id,
            "user_id": user_id,
            # The router delivers to this user in addition to its rule-selected roles.
            "target_user_id": user_id,
            "title": "File verification failed",
            "message": f"{file.filename}: {FAILURE_MESSAGES.get(code, 'The file could not be checked.')}",
            "summary": f"File verification failed: {file.filename}",
            "entity_type": "file",
            "entity_id": file.id,
            "metadata": {"error_code": code},
        },
    )
