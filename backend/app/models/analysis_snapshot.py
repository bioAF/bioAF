from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AnalysisSnapshot(Base):
    __tablename__ = "analysis_snapshots"
    __table_args__ = (
        CheckConstraint(
            "experiment_id IS NOT NULL OR project_id IS NOT NULL",
            name="ck_analysis_snapshots_scope",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    experiment_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("experiments.id"), nullable=True)
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"), nullable=True)
    notebook_session_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("compute_sessions.id"), nullable=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    object_type: Mapped[str] = mapped_column(String(50), nullable=False)
    cell_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gene_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parameters_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    embeddings_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    clusterings_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    layers_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    metadata_columns_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    command_log_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    figure_file_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("files.id"), nullable=True)
    checkpoint_file_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("files.id"), nullable=True)
    starred: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    organization = relationship("Organization")
    experiment = relationship("Experiment")
    project = relationship("Project", back_populates="snapshots")
    notebook_session = relationship("ComputeSession")
    user = relationship("User")
    figure_file = relationship("File", foreign_keys=[figure_file_id])
    checkpoint_file = relationship("File", foreign_keys=[checkpoint_file_id])
