from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


EXPERIMENT_STATUSES = [
    "registered",
    "library_prep",
    "sequencing",
    "fastq_uploaded",
    "processing",
    "pipeline_complete",
    "reviewed",
    "analysis",
    "complete",
]

EXPERIMENT_STATUS_TRANSITIONS = {
    "registered": ["library_prep"],
    "library_prep": ["sequencing"],
    "sequencing": ["fastq_uploaded"],
    "fastq_uploaded": ["processing"],
    "processing": ["pipeline_complete"],
    "pipeline_complete": ["reviewed", "analysis"],
    "reviewed": ["analysis"],
    "analysis": ["complete", "processing"],
    "complete": [],
}


class Experiment(Base):
    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"), nullable=True)
    template_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("experiment_templates.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    hypothesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    protocol_doc_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    owner_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="registered")
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expected_sample_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_unclaimed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    organization = relationship("Organization")
    project = relationship("Project", back_populates="experiments")
    owner = relationship("User")
    template = relationship("ExperimentTemplate")
    samples = relationship("Sample", back_populates="experiment")
    batches = relationship("Batch", back_populates="experiment")
    custom_fields = relationship("ExperimentCustomField", back_populates="experiment")
    field_defaults = relationship("ExperimentFieldDefault", back_populates="experiment")
