from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    experiment_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("experiments.id"), nullable=True)
    submitted_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    pipeline_name: Mapped[str] = mapped_column(String(255), nullable=False)
    pipeline_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    parameters_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    input_files_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output_files_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    container_versions_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    nextflow_trace_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    progress_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    cost_estimate: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    work_dir: Mapped[str | None] = mapped_column(String(500), nullable=True)
    slurm_job_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    k8s_job_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    k8s_namespace: Mapped[str | None] = mapped_column(String(100), nullable=True)
    k8s_pod_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actual_cost: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    reference_genome: Mapped[str | None] = mapped_column(String(200), nullable=True)
    alignment_algorithm: Mapped[str | None] = mapped_column(String(200), nullable=True)
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"), nullable=True)
    resume_from_run_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("pipeline_runs.id"), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    reviewed_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    organization = relationship("Organization")
    experiment = relationship("Experiment")
    project = relationship("Project", back_populates="pipeline_runs")
    submitted_by = relationship("User", foreign_keys=[submitted_by_user_id])
    reviewed_by = relationship("User", foreign_keys=[reviewed_by_user_id])
    resume_from = relationship("PipelineRun", remote_side=[id])
    processes = relationship("PipelineProcess", back_populates="pipeline_run", cascade="all, delete-orphan")
    samples = relationship("Sample", secondary="pipeline_run_samples", viewonly=True)
    references = relationship("ReferenceDataset", secondary="pipeline_run_references", viewonly=True)


class PipelineRunSample(Base):
    __tablename__ = "pipeline_run_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pipeline_run_id: Mapped[int] = mapped_column(Integer, ForeignKey("pipeline_runs.id"), nullable=False)
    sample_id: Mapped[int] = mapped_column(Integer, ForeignKey("samples.id"), nullable=False)
