from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


SEQUENCING_BATCH_STATUSES = [
    "pending",
    "ingesting",
    "complete",
    "partial_complete",
    "failed",
]


class SequencingBatch(Base):
    __tablename__ = "sequencing_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    instrument_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    instrument_platform: Mapped[str | None] = mapped_column(String(100), nullable=True)
    quality_score_encoding: Mapped[str | None] = mapped_column(String(50), server_default="Phred+33", nullable=True)
    sequencer_run_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    manifest_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expected_file_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ingested_file_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    organization = relationship("Organization")
    manifest_entries = relationship("ManifestEntry", back_populates="sequencing_batch")
    files = relationship("File", back_populates="sequencing_batch")
