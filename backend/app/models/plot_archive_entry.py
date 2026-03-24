from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PlotArchiveEntry(Base):
    __tablename__ = "plot_archive"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    file_id: Mapped[int] = mapped_column(Integer, ForeignKey("files.id"), nullable=False)
    experiment_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("experiments.id"), nullable=True)
    pipeline_run_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("pipeline_runs.id"), nullable=True)
    notebook_session_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("compute_sessions.id"), nullable=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    tags_json: Mapped[dict] = mapped_column(JSONB, server_default="[]", nullable=False)
    thumbnail_gcs_uri: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    organization = relationship("Organization")
    file = relationship("File")
    experiment = relationship("Experiment")
    pipeline_run = relationship("PipelineRun")
    notebook_session = relationship("ComputeSession")
