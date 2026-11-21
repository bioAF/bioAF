"""Recorded identity and last observed health of the installation's storage containers.

``StorageIdentity`` holds what bioAF has established about one bucket incarnation: the store it
serves, the storage generation it belongs to, and the provider's native owner and creation evidence.
``basis`` says how it was established: ``discovered`` when provider evidence agreed with a recorded
storage apply, or ``enrolled`` when an administrator acknowledged the observed bucket (a baseline as
of that day, not proof of its earlier history). A retired row is kept for history.

``StorageStoreCheck`` is the latest read-only inspection result per store, for display.
"""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class StorageIdentity(Base):
    __tablename__ = "storage_identities"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    store: Mapped[str] = mapped_column(String(32), nullable=False)
    bucket_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_uid: Mapped[str | None] = mapped_column(String(32), nullable=True)
    owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    basis: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("basis IN ('discovered', 'enrolled')", name="ck_storage_identities_basis"),
        Index(
            "uq_storage_identities_active_bucket",
            "bucket_name",
            unique=True,
            postgresql_where=text("retired_at IS NULL"),
        ),
    )


class StorageStoreCheck(Base):
    __tablename__ = "storage_store_checks"

    store: Mapped[str] = mapped_column(String(32), primary_key=True)
    bucket_name: Mapped[str] = mapped_column(String(255), nullable=False)
    health: Mapped[str] = mapped_column(String(32), nullable=False)
    basis: Mapped[str | None] = mapped_column(String(16), nullable=True)
    observed_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observed_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
