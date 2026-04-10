from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ExperimentAutoRun(Base):
    __tablename__ = "experiment_auto_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    experiment_id: Mapped[int] = mapped_column(Integer, ForeignKey("experiments.id"), nullable=False)
    pipeline_key: Mapped[str] = mapped_column(String(255), nullable=False)
    parameters_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reference_genome: Mapped[str | None] = mapped_column(String(200), nullable=True)
    alignment_algorithm: Mapped[str | None] = mapped_column(String(200), nullable=True)
    delay_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    configured_by_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    organization = relationship("Organization")
    experiment = relationship("Experiment")
    configured_by = relationship("User")
    pending_runs = relationship("PendingAutoRun", back_populates="auto_run_config", cascade="all, delete-orphan")
