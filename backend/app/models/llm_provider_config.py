from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.types import EncryptedString


class LlmProviderConfig(Base):
    __tablename__ = "llm_provider_config"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    api_key: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    api_key_prefix_last5: Mapped[str | None] = mapped_column(String(5), nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    created_by_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    updated_by_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)

    __table_args__ = (
        Index(
            "uq_llm_provider_config_org_provider",
            "organization_id",
            "provider",
            unique=True,
        ),
        Index(
            "uq_llm_provider_config_one_active_per_org",
            "organization_id",
            unique=True,
            postgresql_where="is_active = true",
        ),
    )
