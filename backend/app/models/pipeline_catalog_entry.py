from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PipelineCatalogEntry(Base):
    __tablename__ = "pipeline_catalog"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    pipeline_key: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    schema_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    default_params_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    custom_pipeline_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("custom_pipelines.id"), nullable=True)
    qc_template: Mapped[str | None] = mapped_column(String(50), nullable=True)
    qc_config_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    organization = relationship("Organization")
    custom_pipeline = relationship("CustomPipeline")
