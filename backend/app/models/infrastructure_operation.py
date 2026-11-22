"""One accepted infrastructure action, from admission to its settled outcome.

An operation is the application-level owner of an infrastructure workflow (deploy, teardown,
storage destroy, update, image build, ...): it spans preflight, every Terraform child run, and
cleanup. ``exclusive_slot`` is 1 while the operation holds installation-wide exclusion (accepted,
running, stopping, or recovery required) and NULL once it has settled; the unique constraint on it
is what admits exactly one operation at a time, across requests and restarts.

``runner`` records the backend container and process that ran it, the evidence startup recovery
uses: a confirmed restart of that container proves its local Terraform processes stopped.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

OPERATION_STATUSES = (
    "accepted",
    "running",
    "stopping",
    "succeeded",
    "failed",
    "cancelled",
    "interrupted",
    "recovery_required",
)


class InfrastructureOperation(Base):
    __tablename__ = "infrastructure_operations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    exclusive_slot: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    started_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    started_by_api_key_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True
    )
    principal: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    inputs: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    deployment_generation: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    runner: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    child_run_ids: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    external_builds: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    steps: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    phase: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_category: Mapped[str | None] = mapped_column(String(48), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    partial_effects: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    cancel_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recovered_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    recovery_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("exclusive_slot", name="uq_infrastructure_operations_exclusive_slot"),
        CheckConstraint("exclusive_slot IS NULL OR exclusive_slot = 1", name="ck_infrastructure_operations_slot"),
        CheckConstraint(
            "status IN ('accepted', 'running', 'stopping', 'succeeded', 'failed', 'cancelled', "
            "'interrupted', 'recovery_required')",
            name="ck_infrastructure_operations_status",
        ),
        # One operation per principal and key: overlapping retries cannot both be accepted.
        Index(
            "uq_infrastructure_operations_idempotency",
            "principal",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index("ix_infrastructure_operations_accepted_at", "accepted_at"),
    )
