"""ComparisonTarget: a single quantitative claim from the paper to check (lit_validation B2d).

The ground-truth side of the Level-2 comparison. Each target aligns to a ``QCMetrics`` field where
possible, carries the claimed value + unit, a tolerance (relative unless stated), and where in the
paper the claim came from (for evidence). See ``local/lit_validation/spec-02-data-model.md``.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
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
    # 255, matching `source_locator`: these are a model's reading of a methods section, not a
    # controlled vocabulary, and a real paper wrote "genes (NOTCH4, JAG1, LIFR, CCNA2, CCND2, RB1,
    # SMAD4, JUND, CREBBP)" as the unit of one of its claims.
    unit: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Relative fraction by default (e.g. 0.05 = 5%); the comparison engine (E2) applies the policy.
    tolerance: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_locator: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # plan_6 step 3: how this claim came to be measured against a controlled metric.
    #
    # `metric_key` is the paper's own wording and says nothing about whether it reached a metric or
    # why. The binding is a decision now, so it is recorded like one: what was chosen, the reason, the
    # model's own confidence, and which model made the call. An AI decision that cannot be attributed
    # is a defect, and the feature's output is informational precisely because this is on the record.
    #
    # `bound_key` NULL means the model declined (or never ran); the comparison then falls back to the
    # alias table exactly as it did before, so no existing row changes behaviour.
    bound_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    binding_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    binding_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    bound_by_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # "model" | "human" | "alias_table". NULL means the row predates the column, which is honestly
    # different from a binding the alias table made.
    bound_by: Mapped[str | None] = mapped_column(String(20), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    reproduction_plan = relationship("ReproductionPlan", back_populates="comparison_targets")
