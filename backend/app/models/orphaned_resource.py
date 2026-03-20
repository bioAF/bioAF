from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class OrphanedResource(Base):
    __tablename__ = "orphaned_resources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_name: Mapped[str] = mapped_column(String(255), nullable=False)
    gcp_project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    gcp_zone: Mapped[str | None] = mapped_column(String(100), nullable=True)
    stack_uid: Mapped[str] = mapped_column(String(20), nullable=False)
    terraform_run_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("terraform_runs.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="detected")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
