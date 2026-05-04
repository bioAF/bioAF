from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, String, Table, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# Association table for many-to-many: pipeline_runs <-> reference_datasets
pipeline_run_references = Table(
    "pipeline_run_references",
    Base.metadata,
    Column("pipeline_run_id", Integer, ForeignKey("pipeline_runs.id"), primary_key=True),
    Column("reference_dataset_id", Integer, ForeignKey("reference_datasets.id"), primary_key=True),
)

REFERENCE_CATEGORIES = ["genome", "annotation", "index", "atlas", "markers", "other"]
REFERENCE_SCOPES = ["public", "internal"]
REFERENCE_STATUSES = ["active", "deprecated", "pending_approval", "uploading", "failed"]


class ReferenceDataset(Base):
    __tablename__ = "reference_datasets"
    __table_args__ = (UniqueConstraint("organization_id", "name", "version", name="uq_reference_org_name_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    gcs_prefix: Mapped[str] = mapped_column(Text, nullable=False)
    total_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    file_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    md5_manifest_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    uploaded_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    approved_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")
    deprecation_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    superseded_by_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("reference_datasets.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    organization = relationship("Organization")
    uploaded_by = relationship("User", foreign_keys=[uploaded_by_user_id])
    approved_by = relationship("User", foreign_keys=[approved_by_user_id])
    superseded_by = relationship("ReferenceDataset", remote_side=[id])
    files = relationship("ReferenceDatasetFile", back_populates="reference_dataset", cascade="all, delete-orphan")
    pipeline_runs = relationship("PipelineRun", secondary="pipeline_run_references", viewonly=True)


class ReferenceDatasetFile(Base):
    __tablename__ = "reference_dataset_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reference_dataset_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("reference_datasets.id", ondelete="CASCADE"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    gcs_uri: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    md5_checksum: Mapped[str | None] = mapped_column(String(32), nullable=True)
    file_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    reference_dataset = relationship("ReferenceDataset", back_populates="files")
