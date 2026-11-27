from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.user_summary import UserSummary


# --- Environment schemas ---


class EnvironmentCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    visibility: str = "team"
    environment_type: str = "notebook"


class EnvironmentUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    visibility: str | None = None


class NotebookCapability(BaseModel):
    # The runtime contract version the result was proven under; null when nothing was proven.
    contract: int | None = None
    # verified | unsupported (no R in the environment) | unverified (never proven)
    jupyter: str = "unverified"
    rstudio: str = "unverified"


# Contract 1 proved only that RStudio served its sign-in page, not that it could start an R session.
RSTUDIO_SESSION_CONTRACT = 2


def notebook_capability(raw: dict | None) -> NotebookCapability:
    if not raw:
        return NotebookCapability()
    rstudio = raw.get("rstudio", "unverified")
    contract = raw.get("contract")
    if rstudio == "verified" and (not isinstance(contract, int) or contract < RSTUDIO_SESSION_CONTRACT):
        rstudio = "unverified"
    return NotebookCapability(contract=contract, jupyter=raw.get("jupyter", "unverified"), rstudio=rstudio)


class EnvironmentVersionSummary(BaseModel):
    id: int
    version_number: int
    build_number: int = 1
    status: str
    definition_format: str
    image_uri: str | None = None
    notebook_capability: NotebookCapability = NotebookCapability()
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class EnvironmentResponse(BaseModel):
    id: int
    name: str
    description: str | None = None
    visibility: str
    environment_type: str = "notebook"
    version_count: int = 0
    latest_version: EnvironmentVersionSummary | None = None
    created_by: UserSummary | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class EnvironmentListResponse(BaseModel):
    environments: list[EnvironmentResponse]
    total: int


class EnvironmentDetailResponse(BaseModel):
    id: int
    name: str
    description: str | None = None
    visibility: str
    environment_type: str = "notebook"
    versions: list[EnvironmentVersionSummary]
    created_by: UserSummary | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


# --- Environment Version schemas ---


class VersionCreateRequest(BaseModel):
    definition_format: str = Field(..., pattern="^(dockerfile|conda)$")
    definition_content: str = Field(..., min_length=1)


class VersionResponse(BaseModel):
    id: int
    environment_id: int
    version_number: int
    build_number: int = 1
    status: str
    definition_format: str
    definition_content: str
    build_id: str | None = None
    image_uri: str | None = None
    created_by: UserSummary | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class BuildLogsResponse(BaseModel):
    build_id: str | None = None
    status: str
    logs_url: str | None = None
