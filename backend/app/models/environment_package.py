"""Legacy model for package tracking (pre-ADR-033).

Retained for backward compatibility with package_service and reconciler_service.
These tables are dropped in migration 037.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EnvironmentPackage(Base):
    __tablename__ = "environment_packages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    environment_id: Mapped[int] = mapped_column(Integer, nullable=False)
    package_name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
