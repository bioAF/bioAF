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

import base64
import binascii
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ValidationError
from app.models.file import File
from app.models.file_verification import FileVerification

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

_HEX_MD5 = re.compile(r"^[0-9a-f]{32}$")


def normalize_md5(value: Any, *, allow_base64: bool = False) -> str:
    """Lowercase 32-character hex MD5, or ValidationError.

    ``allow_base64`` accepts the base64 form GCS reports (``md5Hash``); an explicit expectation from a
    caller or CSV must already be hex.
    """
    if isinstance(value, str):
        candidate = value.strip().lower()
        if _HEX_MD5.match(candidate):
            return candidate
        if allow_base64:
            try:
                raw = base64.b64decode(value.strip(), validate=True)
            except (binascii.Error, ValueError):
                raw = b""
            if len(raw) == 16:
                return raw.hex()
    raise ValidationError("MD5 checksum must be 32 hexadecimal characters")


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
        record.next_attempt_at = None
        await session.flush()
        return record

    @staticmethod
    async def await_expectation(session: AsyncSession, file: File, *, source: str, context: dict) -> FileVerification:
        """A source that may carry a checksum is not resolved yet; keep the file from optional readiness."""
        record = await current_record(session, file.id, lock=True)
        if record is not None and record.expected_checksum is not None:
            return record
        if record is None:
            record = FileVerification(organization_id=file.organization_id, file_id=file.id, status=WAITING_EXPECTATION)
            session.add(record)
        record.status = WAITING_EXPECTATION
        record.expectation_source = source
        record.discovery_context = context
        record.next_attempt_at = _now()
        await session.flush()
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
