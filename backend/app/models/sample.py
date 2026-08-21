import uuid as uuid_pkg
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

sample_files = Table(
    "sample_files",
    Base.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("sample_id", Integer, ForeignKey("samples.id"), nullable=False),
    Column("file_id", Integer, ForeignKey("files.id"), nullable=True),
    UniqueConstraint("file_id", "sample_id", name="uq_sample_files_file_sample"),
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

# Controlled vocabulary for the optional, first-class assay field. Kept deliberately small
# (matching what recommend_pipeline maps in v1) and extended additively as pipelines are added.
# "other" records an assay that has no v1 pipeline mapping; a null assay means "infer it".
SAMPLE_ASSAYS = ["bulk_rna", "scrna", "other"]


class Sample(Base):
    __tablename__ = "samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, server_default=text("gen_random_uuid()"), unique=True
    )
    experiment_id: Mapped[int] = mapped_column(Integer, ForeignKey("experiments.id"), nullable=False)
    sample_batch_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("sample_batches.id"), nullable=True)
    sequencing_batch_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("sequencing_batches.id"), nullable=True)
    sequencing_batch_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    organism: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tissue_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    donor_source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Donor sex, for pipelines that require it (nf-core/raredisease). Optional by
    # design: most assays neither use nor need it, so it is never required on
    # intake. A pipeline that needs it blocks with a clear message when empty.
    sex: Mapped[str | None] = mapped_column(String(20), nullable=True)
    treatment_condition: Mapped[str | None] = mapped_column(String(255), nullable=True)
    chemistry_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    viability_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    cell_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prep_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    molecule_type: Mapped[str | None] = mapped_column(String(100), server_default="total RNA", nullable=True)
    library_prep_method: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Optional, first-class assay (controlled vocab: SAMPLE_ASSAYS). When set it is the
    # authoritative signal for recommend_pipeline; when null, the assay is inferred from
    # molecule_type / chemistry_version / library_prep_method.
    assay: Mapped[str | None] = mapped_column(String(50), nullable=True)
    library_layout: Mapped[str | None] = mapped_column(String(50), nullable=True)
    qc_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    qc_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="registered")
    parent_sample_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("samples.id"), nullable=True)
    collection_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    collection_method: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_unclaimed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    experiment = relationship("Experiment", back_populates="samples")
    sample_batch = relationship("SampleBatch", back_populates="samples")
    sequencing_batch = relationship("SequencingBatch")
    files = relationship("File", secondary=sample_files, lazy="select")
    parent_sample = relationship("Sample", remote_side="Sample.id", foreign_keys=[parent_sample_id])
    derived_samples = relationship("Sample", foreign_keys=[parent_sample_id], overlaps="parent_sample")
    custom_fields = relationship("SampleCustomField", back_populates="sample", cascade="all, delete-orphan")
