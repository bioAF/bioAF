from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class NamingProfile(Base):
    __tablename__ = "naming_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    delimiter: Mapped[str] = mapped_column(String(10), nullable=False, server_default="_")
    strip_extension: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    segments_json: Mapped[list] = mapped_column(JSONB, nullable=False)
    # Optional reference to an Experiment Template that seeds this profile's
    # available field vocabulary. Nullable because template selection is
    # optional per the redesign plan.
    experiment_template_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("experiment_templates.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")
    created_by: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    organization = relationship("Organization")
    creator = relationship("User")
    experiment_template = relationship(
        "ExperimentTemplate",
        foreign_keys=[experiment_template_id],
    )
