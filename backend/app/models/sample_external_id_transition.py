from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SampleExternalIdTransition(Base):
    """A sample whose external ID the upgrade assigned (migration 080, plan 9).

    Before 080 a sample's source label (``samples.sample_id_external``) had no uniqueness guarantee.
    Where one experiment held the same label more than once, every one of those samples was given a
    new external ID and recorded here, so people and integrations can still find them by the label
    they knew. Maintained product data: written once by the migration, never by the application.
    """

    __tablename__ = "sample_external_id_transitions"
    __table_args__ = (Index("ix_sample_external_id_transitions_label", "experiment_id", "source_label"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sample_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("samples.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    experiment_id: Mapped[int] = mapped_column(Integer, ForeignKey("experiments.id"), nullable=False)
    source_label: Mapped[str] = mapped_column(String(255), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    revision: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'080'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
