from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SampleBatch(Base):
    __tablename__ = "sample_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experiment_id: Mapped[int] = mapped_column(Integer, ForeignKey("experiments.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    prep_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    operator_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    sequencer_run_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    instrument_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    instrument_platform: Mapped[str | None] = mapped_column(String(100), nullable=True)
    quality_score_encoding: Mapped[str | None] = mapped_column(String(50), server_default="Phred+33", nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    experiment = relationship("Experiment", back_populates="sample_batches")
    operator = relationship("User")
    samples = relationship("Sample", back_populates="sample_batch")
