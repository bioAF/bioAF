from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AgentReview(Base):
    __tablename__ = "agent_reviews"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    triggered_by_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    included_run_ids: Mapped[list[int] | None] = mapped_column(JSONB, nullable=True)
    review_type: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt_template_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", server_default="pending")
    severity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    headline: Mapped[str | None] = mapped_column(Text, nullable=True)
    flags: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)
    evidence: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_gcs_paths: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    agent_review_job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("agent_review_jobs.id"), nullable=False, unique=True
    )
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dismissed_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "ix_agent_reviews_org_entity_created",
            "organization_id",
            "entity_type",
            "entity_id",
            "created_at",
        ),
        Index("ix_agent_reviews_org_status", "organization_id", "status"),
        Index("ix_agent_reviews_org_dismissed", "organization_id", "dismissed_at"),
    )
