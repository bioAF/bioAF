from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AgentReviewPrompt(Base):
    """A named, user-authored review prompt (org-wide visibility).

    Once saved, the prompt appears as an option in the section-builder modal
    for any user in the org with llm_integration:use. Only the creator (or an
    admin) may delete; v1 has no edit-in-place (re-save with a new name).
    """

    __tablename__ = "agent_review_prompts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "uq_agent_review_prompts_org_name",
            "organization_id",
            "name",
            unique=True,
        ),
    )
