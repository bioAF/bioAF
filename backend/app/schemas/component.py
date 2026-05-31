from datetime import datetime

from pydantic import BaseModel


class ComponentDefinition(BaseModel):
    key: str
    name: str
    description: str
    category: str
    dependencies: list[str]
    estimated_monthly_cost: str
    config_schema: list[dict]
    provisioning_time_estimate: str


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


class ComponentConfigUpdate(BaseModel):
    config: dict


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
