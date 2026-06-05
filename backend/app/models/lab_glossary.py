"""Lab Glossary models (ADR-062).

A governed dictionary of lab-specific terminology. Terms are populated three ways
(manual entry, CSV import, LLM scan); every non-manual entry passes through a scan
job that produces proposals reviewed by a human before any write to
``lab_glossary_terms``.
"""

from datetime import datetime

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class LabGlossaryTerm(Base):
    """A committed glossary entry. Uniqueness is case-insensitive per org via the
    functional unique index ``uq_lab_glossary_terms_org_lower_term``."""

    __tablename__ = "lab_glossary_terms"
    __table_args__ = (
        CheckConstraint("source IN ('manual', 'import', 'llm_scan')", name="ck_lab_glossary_terms_source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    term: Mapped[str] = mapped_column(String(500), nullable=False)
    definition: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    category: Mapped[str | None] = mapped_column(String(200), nullable=True)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    organization = relationship("Organization")
    created_by = relationship("User")


class LabGlossaryTermHistory(Base):
    """Append-only record of a term's values prior to an update (ADR-062)."""

    __tablename__ = "lab_glossary_term_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    term_id: Mapped[int] = mapped_column(Integer, ForeignKey("lab_glossary_terms.id"), nullable=False, index=True)
    previous_definition: Mapped[str] = mapped_column(Text, nullable=False)
    previous_aliases: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    previous_category: Mapped[str | None] = mapped_column(String(200), nullable=True)
    previous_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_by_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class LabGlossaryRejectedProposal(Base):
    """A proposal a reviewer rejected or kept-existing. Future scans cross-reference
    this by (organization_id, term, proposed_source) to set ``previously_rejected``.
    No uniqueness: the same term may be rejected repeatedly from different sources."""

    __tablename__ = "lab_glossary_rejected_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    term: Mapped[str] = mapped_column(String(500), nullable=False)
    proposed_definition: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_source: Mapped[str] = mapped_column(String(50), nullable=False)
    rejected_by_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    rejected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class LabGlossaryScanJob(Base):
    """One LLM scan invocation or CSV import. Execution runs in-process following
    the Agent Review pattern (ADR-062): created ``pending``, advanced to ``running``,
    then ``complete``/``failed``."""

    __tablename__ = "lab_glossary_scan_jobs"
    __table_args__ = (
        CheckConstraint(
            "scan_type IN ('document', 'topic', 'platform_wide', 'import')",
            name="ck_lab_glossary_scan_jobs_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'complete', 'failed')",
            name="ck_lab_glossary_scan_jobs_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    scan_type: Mapped[str] = mapped_column(String(20), nullable=False)
    scan_input: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), server_default="pending", nullable=False)
    proposed_new_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proposed_changed_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    accepted_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rejected_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    initiated_by_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    proposals = relationship(
        "LabGlossaryScanProposal", back_populates="scan_job", cascade="all, delete-orphan"
    )


class LabGlossaryScanProposal(Base):
    """A single term/definition proposed by a scan job, awaiting human review."""

    __tablename__ = "lab_glossary_scan_proposals"
    __table_args__ = (
        CheckConstraint("proposal_type IN ('new', 'changed')", name="ck_lab_glossary_proposals_type"),
        CheckConstraint(
            "review_status IN ('pending', 'accepted', 'kept_existing', 'rejected')",
            name="ck_lab_glossary_proposals_review_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scan_job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("lab_glossary_scan_jobs.id"), nullable=False, index=True
    )
    term: Mapped[str] = mapped_column(String(500), nullable=False)
    proposed_definition: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_aliases: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    proposed_category: Mapped[str | None] = mapped_column(String(200), nullable=True)
    proposed_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposal_type: Mapped[str] = mapped_column(String(10), nullable=False)
    existing_term_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("lab_glossary_terms.id"), nullable=True
    )
    source_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    previously_rejected: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    review_status: Mapped[str] = mapped_column(String(20), server_default="pending", nullable=False)
    reviewed_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    scan_job = relationship("LabGlossaryScanJob", back_populates="proposals")
    existing_term = relationship("LabGlossaryTerm")
