from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.integrations.common import CustomFieldIn, CustomFieldOut


class ProjectCreate(BaseModel):
    external_id: str | None = Field(None, max_length=255)
    name: str = Field(..., min_length=1, max_length=255)
    code: str | None = Field(None, max_length=20)
    description: str | None = None
    hypothesis: str | None = None
    custom_fields: list[CustomFieldIn] | None = None


class ProjectUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    hypothesis: str | None = None
    custom_fields: list[CustomFieldIn] | None = None
    # status is intentionally NOT included: status is bioAF-managed


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    external_id: str | None
    name: str
    code: str | None
    description: str | None
    hypothesis: str | None
    status: str | None
    created_at: datetime
    custom_fields: list[CustomFieldOut] = []


class ProjectListOut(BaseModel):
    items: list[ProjectOut]
    next_cursor: str | None = None
