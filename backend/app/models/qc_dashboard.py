from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class QCDashboard(Base):
    __tablename__ = "qc_dashboards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    pipeline_run_id: Mapped[int] = mapped_column(Integer, ForeignKey("pipeline_runs.id"), nullable=False)
    experiment_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("experiments.id"), nullable=True)
    metrics_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    plots_json: Mapped[dict] = mapped_column(JSONB, server_default="[]", nullable=False)
    qc_config_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="generating")
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    organization = relationship("Organization")
    pipeline_run = relationship("PipelineRun")
    experiment = relationship("Experiment")
