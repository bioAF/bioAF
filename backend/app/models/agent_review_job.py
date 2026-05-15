from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AgentReviewJob(Base):
    __tablename__ = "agent_review_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    triggered_by_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    review_type: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt_template_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", server_default="pending")
    included_run_ids: Mapped[list[int] | None] = mapped_column(JSONB, nullable=True)
    include_html_report_run_ids: Mapped[list[int] | None] = mapped_column(JSONB, nullable=True)
    artifact_gcs_paths: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    agent_review_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    pipeline_run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_agent_review_jobs_org_status", "organization_id", "status"),
        Index("ix_agent_review_jobs_entity", "entity_type", "entity_id"),
        Index(
            "uq_agent_review_jobs_inflight_debounce",
            "entity_type",
            "entity_id",
            "review_type",
            unique=True,
            postgresql_where="status IN ('pending', 'building_artifacts', 'submitted')",
        ),
        Index("ix_agent_review_jobs_agent_review_id", "agent_review_id"),
    )
