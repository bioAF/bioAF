from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


# Spec §3: status reported on GET /api/references/{id}/import-status
REFERENCE_IMPORT_STATUSES = [
    "pending",
    "downloading",
    "verifying",
    "extracting",
    "finalizing",
    "active",
    "failed",
]


class ReferenceImportProgress(Base):
    """Per-reference import job state, written by the importer container's
    callback into POST /api/internal/references/{id}/import-progress."""

    __tablename__ = "reference_import_progress"

    reference_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("reference_datasets.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    import_job_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    progress_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bytes_downloaded: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    total_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
