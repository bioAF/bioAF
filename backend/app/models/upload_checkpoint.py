"""A durable record of one upload, from the moment bytes may arrive until they are filed.

Replaces the in-memory pending-upload dictionary. A checkpoint is written before a signed URL is
issued or a proxied transfer starts, so the upload survives a restart and a failed filing step
leaves tracked, recoverable bytes rather than an untracked object.

Stages:
- ``issued``: an upload session (``session``) or, before this release, a presigned URL
  (``signed``) was handed out; the client may still be sending bytes.
- ``transferring``: a proxied upload is streaming through the backend.
- ``received``: the bytes are stored in ingest but not yet filed (registration and move pending).
- ``filed``: the File record exists (``file_id``) and the bytes are where it says.
- ``cancelling``: cancellation was recorded and filing is refused, but the provider has not yet
  confirmed that nothing more can arrive, so the upload still counts as a possible writer.
- ``cancelled``: removed, and nothing more can arrive (or an administrator released it).
- ``failed``: the stored copy was lost; nothing can be filed.

``attempts`` / ``next_attempt_at`` drive bounded automatic filing retries; after the last one the
upload waits for Retry now or Cancel upload (mockup M12). ``reference`` is shown to people and
repeated in the server log.

``session_handle`` is the provider session (a GCS resumable session URL, which is a bearer
credential, or an S3 multipart upload ID): encrypted at rest (ADR-047), never logged or returned in a
listing.
``reconcile_attempts`` / ``next_reconcile_at`` drive background settlement with the provider;
``released_*`` records an administrator's audited release of a writer whose outcome bioAF could
not confirm.
"""

import uuid as uuid_pkg
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.types import EncryptedString


class UploadCheckpoint(Base):
    __tablename__ = "upload_checkpoints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    stage: Mapped[str] = mapped_column(String(24), nullable=False)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    planned_destination: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The File UUID this data-file upload will be registered under, reserved before the first filing
    # attempt so every retry (after a rolled-back savepoint or a restart) files the same identity at
    # the same organized path. An independent UUID, not a reference: the File may not exist yet.
    # NULL for non-file purposes and for checkpoints recorded before reservations existed (one is
    # allocated under the upload lock before such a checkpoint's first new copy).
    file_uuid: Mapped[uuid_pkg.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # Observations committed before a filing copy: the received source's exact version and size
    # (and a trusted whole-object MD5 when the provider has one), and the destination version once
    # the copy is observed. Recovery after a crash compares against these, never against the path.
    source_version: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_observed_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_native_md5: Mapped[str | None] = mapped_column(String(64), nullable=True)
    destination_version: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The stage a cancellation started from. ``received``/``transferring``: the bytes were already
    # stored, so settling is removing bioAF's own copies (the ingest source and any organized copy),
    # with no provider to wait for. NULL for a cancellation of an upload still being sent.
    cancel_from_stage: Mapped[str | None] = mapped_column(String(24), nullable=True)
    received_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expected_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expected_md5: Mapped[str | None] = mapped_column(String(64), nullable=True)
    asserted_md5: Mapped[str | None] = mapped_column(String(64), nullable=True)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    experiment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sample_ids: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    is_global: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reference: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_category: Mapped[str | None] = mapped_column(String(48), nullable=True)
    file_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("files.id", ondelete="SET NULL"), nullable=True)
    grant_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    purpose: Mapped[str] = mapped_column(String(24), nullable=False, server_default=text("'file'"))
    session_protocol: Mapped[str | None] = mapped_column(String(24), nullable=True)
    session_handle: Mapped[str | None] = mapped_column(EncryptedString(), nullable=True)
    progress_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    progress_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # When bioAF first asked the provider to assemble an S3 multipart upload: a later completion
    # checks the upload's actual state before asking again (a completed upload ID is gone).
    assembly_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reconcile_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    next_reconcile_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    release_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "stage IN ('issued', 'transferring', 'received', 'filed', 'cancelling', 'cancelled', 'failed')",
            name="ck_upload_checkpoints_stage",
        ),
        CheckConstraint("kind IN ('signed', 'proxied', 'session')", name="ck_upload_checkpoints_kind"),
        CheckConstraint("purpose IN ('file', 'lab_document', 'reference')", name="ck_upload_checkpoints_purpose"),
        Index("ix_upload_checkpoints_org_stage", "organization_id", "stage"),
        Index("uq_upload_checkpoints_file_uuid", "file_uuid", unique=True),
    )
