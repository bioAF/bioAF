"""A durable record of one upload, from the moment bytes may arrive until they are filed.

Replaces the in-memory pending-upload dictionary. A checkpoint is written before a signed URL is
issued or a proxied transfer starts, so the upload survives a restart and a failed filing step
leaves tracked, recoverable bytes rather than an untracked object.

Stages:
- ``issued``: a signed URL was handed out; the client may still be sending bytes.
- ``transferring``: a proxied upload is streaming through the backend.
- ``received``: the bytes are stored in ingest but not yet filed (registration and move pending).
- ``filed``: the File record exists (``file_id``) and the bytes are where it says.
- ``cancelled``: the uploader or an administrator removed it.
- ``failed``: the stored copy was lost; nothing can be filed.

``attempts`` / ``next_attempt_at`` drive bounded automatic filing retries; after the last one the
upload waits for Retry now or Cancel upload (mockup M12). ``reference`` is shown to people and
repeated in the server log.
"""

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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "stage IN ('issued', 'transferring', 'received', 'filed', 'cancelled', 'failed')",
            name="ck_upload_checkpoints_stage",
        ),
        CheckConstraint("kind IN ('signed', 'proxied')", name="ck_upload_checkpoints_kind"),
        Index("ix_upload_checkpoints_org_stage", "organization_id", "stage"),
    )
