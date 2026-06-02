from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ExperimentTemplate(Base):
    __tablename__ = "experiment_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    required_fields_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    custom_fields_schema_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Optional default naming profile for experiments built from this
    # template. Inherited at experiment-create time; experiments can
    # override. See ADR-058.
    naming_profile_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("naming_profiles.id"), nullable=True
    )
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    organization = relationship("Organization")
    created_by = relationship("User")
    naming_profile = relationship("NamingProfile", foreign_keys=[naming_profile_id])
