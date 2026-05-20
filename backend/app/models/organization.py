from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.types import EncryptedString


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    setup_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    smtp_configured: Mapped[bool] = mapped_column(Boolean, default=False)
    smtp_host: Mapped[str] = mapped_column(String(255), default="")
    smtp_port: Mapped[int] = mapped_column(Integer, default=587)
    smtp_username: Mapped[str] = mapped_column(String(255), default="")
    smtp_password: Mapped[str] = mapped_column(EncryptedString, default="")
    smtp_from_address: Mapped[str] = mapped_column(String(255), default="")
    smtp_encryption: Mapped[str] = mapped_column(String(20), default="starttls")
    slack_client_id: Mapped[str] = mapped_column(String(255), default="")
    slack_client_secret: Mapped[str] = mapped_column(EncryptedString, default="")
    slack_signing_secret: Mapped[str] = mapped_column(EncryptedString, default="")
    setup_code_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, default=None)
    setup_code_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    lit_review_relevance_threshold: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.65, server_default="0.65"
    )
    # Automated AI Lit Review cadence (Settings > Integrations > LLMs). When
    # enabled, a background loop runs Lit Review Runs for experiments with new
    # activity since their last automated run, at most max_runs_per_tick per tick.
    lit_review_auto_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    lit_review_auto_cadence: Mapped[str] = mapped_column(
        String(16), nullable=False, default="weekly", server_default="weekly"
    )
    lit_review_max_runs_per_tick: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default="5"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
