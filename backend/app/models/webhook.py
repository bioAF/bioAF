from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, SmallInteger, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.types import EncryptedString


class WebhookSubscription(Base):
    __tablename__ = "webhook_subscriptions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    secret: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    events: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_by_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    deliveries = relationship("WebhookDelivery", back_populates="subscription")


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    subscription_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("webhook_subscriptions.id"), nullable=False, index=True
    )
    event_id: Mapped[str] = mapped_column(String(32), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_response_status: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    last_response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    subscription = relationship("WebhookSubscription", back_populates="deliveries")

    __table_args__ = (
        Index("ix_webhook_deliveries_status_next", "status", "next_attempt_at"),
    )
