from datetime import datetime

from pydantic import BaseModel, Field


class UserSummary(BaseModel):
    id: int
    name: str | None = None
    email: str


# --- Environment schemas ---


class EnvironmentCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    visibility: str = "team"


class EnvironmentUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    visibility: str | None = None


class EnvironmentVersionSummary(BaseModel):
    id: int
    version_number: int
    status: str
    definition_format: str
    image_uri: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class EnvironmentResponse(BaseModel):
    id: int
    name: str
    description: str | None = None
    visibility: str
    version_count: int = 0
    latest_version: EnvironmentVersionSummary | None = None
    created_by: UserSummary | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EnvironmentListResponse(BaseModel):
    environments: list[EnvironmentResponse]
    total: int


class EnvironmentDetailResponse(BaseModel):
    id: int
    name: str
    description: str | None = None
    visibility: str
    versions: list[EnvironmentVersionSummary]
    created_by: UserSummary | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# --- Environment Version schemas ---


class VersionCreateRequest(BaseModel):
    definition_format: str = Field(..., pattern="^(dockerfile|conda)$")
    definition_content: str = Field(..., min_length=1)


class VersionResponse(BaseModel):
    id: int
    environment_id: int
    version_number: int
    status: str
    definition_format: str
    definition_content: str
    build_id: str | None = None
    image_uri: str | None = None
    created_by: UserSummary | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class BuildLogsResponse(BaseModel):
    build_id: str | None = None
    status: str
    logs_url: str | None = None
