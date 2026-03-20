"""Pydantic schemas for orphaned resource API responses."""

from datetime import datetime

from pydantic import BaseModel


class OrphanedResourceResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    resource_type: str
    resource_name: str
    gcp_project_id: str
    gcp_zone: str | None
    stack_uid: str
    terraform_run_id: int | None
    status: str
    error_message: str | None
    detected_at: datetime
    resolved_at: datetime | None
    resolved_by_user_id: int | None


class OrphanedResourceListResponse(BaseModel):
    items: list[OrphanedResourceResponse]
    total: int
