from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# Fields that can have experiment-level defaults applied to samples
DEFAULTABLE_SAMPLE_FIELDS = [
    "organism",
    "tissue_type",
    "donor_source",
    "treatment_condition",
    "chemistry_version",
    "molecule_type",
    "library_prep_method",
    "library_layout",
]


class ExperimentFieldDefault(Base):
    __tablename__ = "experiment_field_defaults"
    __table_args__ = (UniqueConstraint("experiment_id", "field_name", name="uq_experiment_field_defaults_exp_field"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experiment_id: Mapped[int] = mapped_column(Integer, ForeignKey("experiments.id"), nullable=False)
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    default_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_required: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    experiment = relationship("Experiment", back_populates="field_defaults")
