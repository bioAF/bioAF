from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ComputeSession(Base):
    __tablename__ = "compute_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    session_type: Mapped[str] = mapped_column(String(20), nullable=False)
    experiment_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("experiments.id"), nullable=True)
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"), nullable=True)
    slurm_job_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    resource_profile: Mapped[str] = mapped_column(String(50), nullable=False)
    cpu_cores: Mapped[int] = mapped_column(Integer, nullable=False)
    memory_gb: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    idle_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    proxy_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    k8s_pod_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    k8s_namespace: Mapped[str | None] = mapped_column(String(100), nullable=True)
    access_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    gcs_home_prefix: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # New columns for work node support (ADR-034)
    environment_version_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("environment_versions.id"), nullable=True
    )
    machine_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    data_mount_paths: Mapped[list | None] = mapped_column(JSON, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_token: Mapped[str | None] = mapped_column(String(255), nullable=True)
    git_branch_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    git_commit_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    user = relationship("User")
    organization = relationship("Organization")
    experiment = relationship("Experiment")
    project = relationship("Project")
    environment_version = relationship("EnvironmentVersion")
    accessed_files = relationship("NotebookSessionFile", foreign_keys="NotebookSessionFile.session_id")


# Backwards-compatible alias for existing imports
NotebookSession = ComputeSession
