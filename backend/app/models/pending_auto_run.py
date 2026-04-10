from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

PENDING_AUTO_RUN_STATUSES = [
    "waiting",
    "launched",
    "cancelled",
]


class PendingAutoRun(Base):
    __tablename__ = "pending_auto_runs"
    __table_args__ = (
        UniqueConstraint("auto_run_config_id", "sample_id", name="uq_pending_auto_run_config_sample"),
        Index("ix_pending_auto_run_status_scheduled", "status", "scheduled_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    auto_run_config_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("experiment_auto_runs.id", ondelete="CASCADE"), nullable=False
    )
    experiment_id: Mapped[int] = mapped_column(Integer, ForeignKey("experiments.id"), nullable=False)
    sample_id: Mapped[int] = mapped_column(Integer, ForeignKey("samples.id"), nullable=False)
    sample_completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="waiting")
    pipeline_run_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("pipeline_runs.id"), nullable=True)
    cancelled_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    organization = relationship("Organization")
    auto_run_config = relationship("ExperimentAutoRun", back_populates="pending_runs")
    experiment = relationship("Experiment")
    sample = relationship("Sample")
    pipeline_run = relationship("PipelineRun")
