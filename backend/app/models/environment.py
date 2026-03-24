from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Environment(Base):
    __tablename__ = "environments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    visibility: Mapped[str] = mapped_column(String(50), nullable=False, server_default="team")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    organization = relationship("Organization", lazy="selectin")
    created_by = relationship("User", lazy="selectin")
    versions = relationship("EnvironmentVersion", back_populates="environment", lazy="selectin")
