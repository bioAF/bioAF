from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class CustomPipelineVersion(Base):
    __tablename__ = "custom_pipeline_versions"
    __table_args__ = (
        UniqueConstraint("custom_pipeline_id", "version_number", name="uq_custom_pipeline_version_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    custom_pipeline_id: Mapped[int] = mapped_column(Integer, ForeignKey("custom_pipelines.id"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    code_source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    github_repo_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("github_repos.id"), nullable=True)
    code_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    entrypoint_command: Mapped[str] = mapped_column(Text, nullable=False)
    environment_version_id: Mapped[int] = mapped_column(Integer, ForeignKey("environment_versions.id"), nullable=False)
    cpu_request: Mapped[str] = mapped_column(String(20), nullable=False, server_default="2")
    memory_request: Mapped[str] = mapped_column(String(20), nullable=False, server_default="8Gi")
    log_file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    version_trigger: Mapped[str] = mapped_column(String(20), nullable=False, server_default="user")
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="active")
    created_by_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    custom_pipeline = relationship("CustomPipeline", back_populates="versions")
    environment_version = relationship("EnvironmentVersion", lazy="selectin")
    github_repo = relationship("GitHubRepo", lazy="selectin")
    created_by = relationship("User", lazy="selectin")
    variables = relationship("CustomPipelineVariable", back_populates="version", cascade="all, delete-orphan")
