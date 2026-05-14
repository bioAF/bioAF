from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.integrations.common import CustomFieldIn, CustomFieldOut


class ExperimentCreate(BaseModel):
    external_id: str = Field(..., min_length=1, max_length=255)
    name: str = Field(..., min_length=1, max_length=255)
    project_id: int | None = None
    project_external_id: str | None = Field(None, max_length=255)
    hypothesis: str | None = None
    description: str | None = None
    expected_sample_count: int | None = None
    variables_json: dict | None = None
    custom_fields: list[CustomFieldIn] | None = None
    # status is intentionally NOT accepted; creates are forced to "registered"


class ExperimentUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    hypothesis: str | None = None
    description: str | None = None
    expected_sample_count: int | None = None
    variables_json: dict | None = None
    custom_fields: list[CustomFieldIn] | None = None
    # status intentionally NOT accepted; PATCH rejects status writes


class ExperimentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    external_id: str | None
    name: str
    code: str | None
    project_id: int | None
    status: str
    hypothesis: str | None
    description: str | None
    expected_sample_count: int | None
    variables_json: dict | None
    created_at: datetime
    custom_fields: list[CustomFieldOut] = []


class ExperimentListOut(BaseModel):
    items: list[ExperimentOut]
    next_cursor: str | None = None
