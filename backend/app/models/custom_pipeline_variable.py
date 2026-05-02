from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class CustomPipelineVariable(Base):
    __tablename__ = "custom_pipeline_variables"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    custom_pipeline_version_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("custom_pipeline_versions.id"), nullable=False
    )
    variable_name: Mapped[str] = mapped_column(String(255), nullable=False)
    default_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    variable_type: Mapped[str] = mapped_column(String(50), nullable=False, server_default="string")
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    version = relationship("CustomPipelineVersion", back_populates="variables")
