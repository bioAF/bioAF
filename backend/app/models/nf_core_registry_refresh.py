from datetime import datetime

from sqlalchemy import DateTime, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class NfCoreRegistryRefresh(Base):
    """Singleton row (id=1) tracking the last registry refresh attempt."""

    __tablename__ = "nf_core_registry_refresh"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
