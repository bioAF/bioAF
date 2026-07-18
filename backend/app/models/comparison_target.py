"""ComparisonTarget: a single quantitative claim from the paper to check (lit_validation B2d).

The ground-truth side of the Level-2 comparison. Each target aligns to a ``QCMetrics`` field where
possible, carries the claimed value + unit, a tolerance (relative unless stated), and where in the
paper the claim came from (for evidence). See ``local/lit_validation/spec-02-data-model.md``.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ComparisonTarget(Base):
    __tablename__ = "comparison_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reproduction_plan_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("reproduction_plans.id"), nullable=False, index=True
    )

    metric_key: Mapped[str] = mapped_column(String(100), nullable=False)
    claimed_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Relative fraction by default (e.g. 0.05 = 5%); the comparison engine (E2) applies the policy.
    tolerance: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_locator: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    reproduction_plan = relationship("ReproductionPlan", back_populates="comparison_targets")
