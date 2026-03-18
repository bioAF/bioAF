from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, Table, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

sample_files = Table(
    "sample_files",
    Base.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("sample_id", Integer, ForeignKey("samples.id"), nullable=False),
    Column("file_id", Integer, ForeignKey("files.id"), nullable=True),
    extend_existing=True,
)


SAMPLE_STATUSES = [
    "registered",
    "library_prepped",
    "sequenced",
    "fastq_uploaded",
    "pipeline_complete",
    "analysis_complete",
]

QC_STATUSES = ["pass", "warning", "fail"]


class Sample(Base):
    __tablename__ = "samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experiment_id: Mapped[int] = mapped_column(Integer, ForeignKey("experiments.id"), nullable=False)
    batch_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("batches.id"), nullable=True)
    sample_id_external: Mapped[str | None] = mapped_column(String(255), nullable=True)
    organism: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tissue_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    donor_source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    treatment_condition: Mapped[str | None] = mapped_column(String(255), nullable=True)
    chemistry_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    viability_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    cell_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prep_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    molecule_type: Mapped[str | None] = mapped_column(String(100), server_default="total RNA", nullable=True)
    library_prep_method: Mapped[str | None] = mapped_column(String(200), nullable=True)
    library_layout: Mapped[str | None] = mapped_column(String(50), nullable=True)
    qc_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    qc_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="registered")
    is_unclaimed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    experiment = relationship("Experiment", back_populates="samples")
    batch = relationship("Batch", back_populates="samples")
    files = relationship("File", secondary=sample_files, lazy="select")
