from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class DashboardLayout(Base):
    """One row per user holding their customizable dashboard widget selection.

    The store is intentionally opaque: ``widgets`` is a JSON array of
    ``{"key": str, "settings": dict}`` entries. The backend never validates the
    widget keys, so the frontend widget catalog can evolve without a migration.
    Absence of a row means the user has never configured their dashboard, so the
    frontend seeds the role default.
    """

    __tablename__ = "dashboard_layouts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    widgets: Mapped[list] = mapped_column(JSONB, server_default="[]", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user = relationship("User")
