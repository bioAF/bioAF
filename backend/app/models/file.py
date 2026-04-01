from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class File(Base):
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    gcs_uri: Mapped[str] = mapped_column(String(1000), nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    md5_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    upload_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    uploader_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    file_type: Mapped[str] = mapped_column(String(50), nullable=False)
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"), nullable=True)
    tags_json: Mapped[dict] = mapped_column(JSONB, server_default="[]", nullable=False)
    file_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ingest_source: Mapped[str | None] = mapped_column(String(20), server_default="manual", nullable=True)
    experiment_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("experiments.id"), nullable=True, index=True)
    # Valid source_type values: "upload", "pipeline_output", "notebook_output"
    source_type: Mapped[str] = mapped_column(String(30), server_default="upload", nullable=False)
    source_pipeline_run_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("pipeline_runs.id"), nullable=True)
    source_notebook_session_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("compute_sessions.id"), nullable=True
    )
    sha256_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    artifact_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    storage_deleted: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("idx_files_experiment_id", "experiment_id"),)

    organization = relationship("Organization")
    uploader = relationship("User")
    project = relationship("Project")
    experiment = relationship("Experiment")
    source_pipeline_run = relationship("PipelineRun", foreign_keys=[source_pipeline_run_id])
    source_notebook_session = relationship("ComputeSession", foreign_keys=[source_notebook_session_id])
    consumed_by_runs = relationship("PipelineRunInputFile", cascade="all, delete-orphan", passive_deletes=True)
    notebook_sessions = relationship("NotebookSessionFile", foreign_keys="NotebookSessionFile.file_id")
