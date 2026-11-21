"""What infrastructure definitions this installation has reconciled, and the notice about the rest.

``InfrastructureDefinitionBaseline``: per cloud and module, the definition fingerprint of the last
operation that fully reconciled it (a deploy of the module, or a full infrastructure update). A
targeted or partial apply never advances it. ``destructive_pending`` records that the additive
changes were applied but delete/replace changes remain (they take effect on redeploy).

``InfrastructureDefinitionNotice``: the pending definition set an admin is told about after an
update (mockup M17). Its ``revision`` identifies the pending set, so an application-only release
with the same pending definitions keeps the same notice, and a new pending set is a new notice.

``InfrastructureNoticeAcknowledgment``: one admin's "Later" for one notice. It never marks anything
applied, and one admin's acknowledgment is never another's.
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class InfrastructureDefinitionBaseline(Base):
    __tablename__ = "infrastructure_definition_baselines"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cloud: Mapped[str] = mapped_column(String(16), nullable=False)
    module: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    destructive_pending: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    operation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("infrastructure_operations.id", ondelete="SET NULL"), nullable=True
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("cloud", "module", name="uq_infrastructure_definition_baselines_module"),)


class InfrastructureDefinitionNotice(Base):
    __tablename__ = "infrastructure_definition_notices"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    revision: Mapped[str] = mapped_column(String(64), nullable=False)
    release_version: Mapped[str] = mapped_column(String(64), nullable=False)
    pending_modules: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    unknown_baseline: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InfrastructureNoticeAcknowledgment(Base):
    __tablename__ = "infrastructure_notice_acknowledgments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    notice_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("infrastructure_definition_notices.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    acknowledged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (UniqueConstraint("notice_id", "user_id", name="uq_infrastructure_notice_ack_user"),)
