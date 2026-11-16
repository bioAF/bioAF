"""The current checksum verification record of one file (one row per file).

Expected and observed values are kept apart: an expectation is what a caller or source asserted (an
upload's MD5, a fetchngs CSV value); an observation is a digest bioAF read independently from the
final stored object. Provider metadata is never compared with itself. Work state (attempts, lease,
progress, safe error code) is durable, so pending verification survives request completion and
restarts. See app/services/file_verification_service.py for the status vocabulary and transitions.
"""

import uuid as uuid_pkg
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class FileVerification(Base):
    __tablename__ = "file_verifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    file_id: Mapped[int] = mapped_column(Integer, ForeignKey("files.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    # Where the expectation came from (upload_assertion, fetchngs_csv) and its revision: a changed
    # expectation bumps the revision, which invalidates any in-flight result computed for the old one.
    expectation_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    expectation_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    expected_algorithm: Mapped[str | None] = mapped_column(String(16), nullable=True)
    expected_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Whether the checksum requirement is known: an MD5 was recorded, or the source was resolved as
    # carrying none. False while discovery is unresolved, including after discovery itself failed or
    # timed out, so a retry rediscovers instead of treating an unknown expectation as "none".
    expectation_resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # Enough source context (run, CSV location) to retry expectation discovery after a restart.
    discovery_context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    observed_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observed_method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    observed_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    observed_uri: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    observed_object_version: Mapped[str | None] = mapped_column(String(255), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bytes_processed: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    total_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_token: Mapped[uuid_pkg.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    retryable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # The NFS protected copy this attempt owns, recorded before any I/O so cleanup can tell an orphan
    # from an artifact in use.
    artifact_uuid: Mapped[uuid_pkg.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    artifact_uri: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("file_id", name="uq_file_verifications_file"),
        Index("ix_file_verifications_due", "status", "next_attempt_at"),
    )
