from datetime import datetime

from pydantic import BaseModel


class ComponentStateResponse(BaseModel):
    key: str
    name: str
    description: str
    category: str
    enabled: bool
    status: str
    config: dict
    dependencies: list[str]
    estimated_monthly_cost: str
    updated_at: datetime | None = None


class ComponentListResponse(BaseModel):
    components: list[ComponentStateResponse]


class ComponentSelectBatchRequest(BaseModel):
    keys: list[str]


class ComponentSelectBatchResponse(BaseModel):
    queued: list[str]


class TerraformRunResponse(BaseModel):
    id: int
    triggered_by_user_id: int
    action: str
    component_key: str | None
    plan_summary: dict | None
    status: str
    started_at: datetime
    completed_at: datetime | None
    error_message: str | None

    model_config = {"from_attributes": True}


class TerraformRunListResponse(BaseModel):
    runs: list[TerraformRunResponse]
    total: int
