from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


MANIFEST_ENTRY_STATUSES = [
    "pending",
    "verified",
    "checksum_mismatch",
    "missing",
    "failed",
]


class ManifestEntry(Base):
    __tablename__ = "manifest_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sequencing_batch_id: Mapped[int] = mapped_column(Integer, ForeignKey("sequencing_batches.id"), nullable=False)
    expected_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    expected_md5: Mapped[str] = mapped_column(String(64), nullable=False)
    resolved_sample_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("samples.id"), nullable=True)
    resolved_experiment_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("experiments.id"), nullable=True)
    resolved_project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"), nullable=True)
    file_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("files.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    sequencing_batch = relationship("SequencingBatch", back_populates="manifest_entries")
    resolved_sample = relationship("Sample")
    resolved_experiment = relationship("Experiment")
    resolved_project = relationship("Project")
    file = relationship("File")
