"""Scientific Decision Record (SDR) models (ADR-059, ADR-063, ADR-064).

SDRs are structured records of significant scientific decisions, modeled on ADRs.
Each SDR carries an immutable org-scoped ``sdr_number`` (allocated via CodeService),
moves through a guarded status machine, and keeps an append-only transition log.
An optional ``trigger_date`` drives the daily re-assessment loop (ADR-064).
"""

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# Valid SDR statuses (ADR-063). Kept here so the model, migration, and service
# transition guard reference one source.
SDR_STATUSES = ("draft", "active", "flagged_for_review", "superseded", "repealed")


class SdrCategory(Base):
    """Admin-controlled SDR category vocabulary (ADR-063). Org-scoped, dedicated
    table (the global ``controlled_vocabularies`` table is not org-scoped)."""

    __tablename__ = "sdr_categories"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_sdr_categories_org_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    created_by = relationship("User")


class ScientificDecisionRecord(Base):
    """A scientific decision record (ADR-063). ``sdr_number`` is immutable and
    org-scoped; supersession links are bidirectional."""

    __tablename__ = "scientific_decision_records"
    __table_args__ = (
        UniqueConstraint("organization_id", "sdr_number", name="uq_sdr_org_number"),
        CheckConstraint(
            "status IN ('draft', 'active', 'flagged_for_review', 'superseded', 'repealed')",
            name="ck_sdr_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    sdr_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), server_default="draft", nullable=False)
    category_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("sdr_categories.id"), nullable=True)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    owner_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    # Re-assessment trigger (ADR-064). ``trigger_warning_sent_at`` tracks the
    # once-only 7-day advance warning; it is cleared when ``trigger_date`` changes.
    trigger_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    trigger_warning_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Bidirectional supersession links.
    superseded_by_sdr_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("scientific_decision_records.id"), nullable=True
    )
    supersedes_sdr_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("scientific_decision_records.id"), nullable=True
    )

    category = relationship("SdrCategory")
    owner = relationship("User", foreign_keys=[owner_user_id])
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    superseded_by = relationship(
        "ScientificDecisionRecord", foreign_keys=[superseded_by_sdr_id], remote_side=[id], post_update=True
    )
    supersedes = relationship(
        "ScientificDecisionRecord", foreign_keys=[supersedes_sdr_id], remote_side=[id], post_update=True
    )
    transitions = relationship(
        "SdrStatusTransition",
        back_populates="sdr",
        cascade="all, delete-orphan",
        order_by="SdrStatusTransition.transitioned_at",
    )


class SdrStatusTransition(Base):
    """Append-only transition / edit-note log for an SDR (ADR-063). Rows are
    written for every status change and for edits to an active SDR's
    decision/justification (recorded as a note, not a status change)."""

    __tablename__ = "sdr_status_transitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sdr_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("scientific_decision_records.id"), nullable=False, index=True
    )
    from_status: Mapped[str] = mapped_column(String(20), nullable=False)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    transitioned_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    transitioned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    sdr = relationship("ScientificDecisionRecord", back_populates="transitions")
    transitioned_by = relationship("User")
