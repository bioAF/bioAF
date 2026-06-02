from datetime import datetime

from pydantic import BaseModel

from app.schemas.experiment import UserSummary


class TemplateCreate(BaseModel):
    name: str
    description: str | None = None
    required_fields_json: dict | None = None
    custom_fields_schema_json: dict | None = None
    naming_profile_id: int | None = None


class TemplateUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    required_fields_json: dict | None = None
    custom_fields_schema_json: dict | None = None
    naming_profile_id: int | None = None


class TemplateResponse(BaseModel):
    id: int
    name: str
    description: str | None
    required_fields_json: dict | None
    custom_fields_schema_json: dict | None
    naming_profile_id: int | None = None
    created_by: UserSummary | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
