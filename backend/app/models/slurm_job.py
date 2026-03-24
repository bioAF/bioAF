from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SlurmJob(Base):
    __tablename__ = "slurm_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    slurm_job_id: Mapped[str] = mapped_column(String(50), nullable=False)
    job_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    partition: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    experiment_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("experiments.id"), nullable=True)
    notebook_session_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("compute_sessions.id"), nullable=True)
    cpu_requested: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_gb_requested: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cpu_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_gb_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stdout_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    stderr_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    cost_estimate: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user = relationship("User")
    organization = relationship("Organization")
    experiment = relationship("Experiment")
    notebook_session = relationship("ComputeSession")
