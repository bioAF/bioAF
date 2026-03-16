from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class BudgetConfig(Base):
    __tablename__ = "budget_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False, unique=True)
    monthly_budget: Mapped[Decimal | None] = mapped_column(Numeric(precision=12, scale=2), nullable=True)
    threshold_50_enabled: Mapped[bool] = mapped_column(Boolean, server_default="true", nullable=False)
    threshold_80_enabled: Mapped[bool] = mapped_column(Boolean, server_default="true", nullable=False)
    threshold_100_enabled: Mapped[bool] = mapped_column(Boolean, server_default="true", nullable=False)
    scale_to_zero_on_100: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    currency: Mapped[str] = mapped_column(String(3), server_default="USD", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    organization = relationship("Organization")
