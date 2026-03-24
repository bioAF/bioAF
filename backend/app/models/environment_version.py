from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class EnvironmentVersion(Base):
    __tablename__ = "environment_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    environment_id: Mapped[int] = mapped_column(Integer, ForeignKey("environments.id"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="draft")
    definition_format: Mapped[str] = mapped_column(String(50), nullable=False)
    definition_content: Mapped[str] = mapped_column(Text, nullable=False)
    build_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    image_uri: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    environment = relationship("Environment", back_populates="versions")
    created_by = relationship("User", lazy="selectin")
