"""plan_8_1 section 3.2: one durable record per check a study runs, kept through every revision.

Evidence artifacts were single slots per study (``level3``, ``level3_result``, ``classification_result``,
``author_consistency``), each moved to history when the selection revision changed. Running a second
check of the same kind overwrote the first, so two contrasts could never keep two results.

A record is keyed by a stable ``check_id``: the plan, the comparison target and the check kind. It
survives attempts and revisions. ``dependencies`` is what the outcome depends on (the claim's predicate,
the input and its checksum, the sample mapping, the reference, the workflow, its version and parameters,
the source listing); a change to one of them supersedes only the records that depend on it, and each
superseded revision is kept whole in ``history``. A workflow check carries the ``analysis_key`` its
execution is fingerprinted by and the ``approval_id`` that covers it; checks sharing an ``analysis_key``
share one execution.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ValidationCheckRecord(Base):
    __tablename__ = "validation_check_records"
    __table_args__ = (UniqueConstraint("check_id", name="uq_validation_check_records_check_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    validation_study_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("validation_studies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reproduction_plan_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("reproduction_plans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    comparison_target_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("comparison_targets.id", ondelete="CASCADE"), nullable=True
    )
    # Stable across attempts and revisions: the plan, the comparison target and the check kind.
    check_id: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    dependencies_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    dependencies_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    analysis_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    approval_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # pending | running | done | blocked | unresolved | interrupted | superseded
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    attempts_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    outcome_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    history_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
