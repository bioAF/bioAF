from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    setup_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    smtp_configured: Mapped[bool] = mapped_column(Boolean, default=False)
    smtp_host: Mapped[str] = mapped_column(String(255), default="")
    smtp_port: Mapped[int] = mapped_column(Integer, default=587)
    smtp_username: Mapped[str] = mapped_column(String(255), default="")
    smtp_password: Mapped[str] = mapped_column(String(500), default="")
    smtp_from_address: Mapped[str] = mapped_column(String(255), default="")
    smtp_encryption: Mapped[str] = mapped_column(String(20), default="starttls")
    slack_client_id: Mapped[str] = mapped_column(String(255), default="")
    slack_client_secret: Mapped[str] = mapped_column(String(500), default="")
    slack_signing_secret: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
