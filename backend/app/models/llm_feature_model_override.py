"""Per-feature LLM model override (plan_6 step 6).

Names a model for one feature, on a provider the org has ALREADY configured. The API key is not
stored here: it stays on that provider's ``llm_provider_config`` row, so a key is rotated in one
place and an override can never hold a stale secret.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class LlmFeatureModelOverride(Base):
    __tablename__ = "llm_feature_model_override"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    # One of app.services.llm_feature_models.VALID_FEATURES.
    feature: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    updated_by_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)

    __table_args__ = (Index("uq_llm_feature_model_override_org_feature", "organization_id", "feature", unique=True),)
