from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class DailyCost(BaseModel):
    date: date
    amount: Decimal


class ComponentCost(BaseModel):
    component: str
    amount: Decimal
    percentage: float


class UserCost(BaseModel):
    user_id: int
    email: str
    name: str | None = None
    amount: Decimal


class CostSummaryResponse(BaseModel):
    current_month_spend: Decimal
    daily_trend: list[DailyCost]
    breakdown_by_component: list[ComponentCost]
    per_user: list[UserCost]
    monthly_budget: Decimal | None = None
    budget_remaining: Decimal | None = None
    projected_month_end: Decimal | None = None
    currency: str = "USD"


class CostHistoryResponse(BaseModel):
    records: list[DailyCost]
    total_amount: Decimal


class BudgetConfigResponse(BaseModel):
    monthly_budget: Decimal | None = None
    threshold_50_enabled: bool
    threshold_80_enabled: bool
    threshold_100_enabled: bool
    scale_to_zero_on_100: bool
    currency: str = "USD"

    model_config = {"from_attributes": True}


class BudgetConfigUpdate(BaseModel):
    monthly_budget: Decimal | None = None
    threshold_50_enabled: bool | None = None
    threshold_80_enabled: bool | None = None
    threshold_100_enabled: bool | None = None
    scale_to_zero_on_100: bool | None = None
    currency: str | None = None
