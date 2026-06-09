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
