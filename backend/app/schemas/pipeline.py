from datetime import datetime

from pydantic import BaseModel


class PipelineCatalogResponse(BaseModel):
    id: int
    pipeline_key: str
    name: str
    description: str | None = None
    source_type: str
    source_url: str | None = None
    version: str | None = None
    parameter_schema: dict | None = None
    default_params: dict | None = None
    is_builtin: bool
    enabled: bool
    custom_pipeline_id: int | None = None
    created_by_username: str | None = None
    latest_version_number: int | None = None

    model_config = {"from_attributes": True}


class PipelineCatalogListResponse(BaseModel):
    pipelines: list[PipelineCatalogResponse]
    total: int


class PipelineAddRequest(BaseModel):
    name: str
    source_url: str
    version: str | None = None
    description: str | None = None


class PipelineVersionUpdateRequest(BaseModel):
    version: str


class RegistryPipelineItem(BaseModel):
    name: str
    full_name: str
    description: str | None = None
    topics: list[str] = []
    stars: int | None = None
    latest_release: str | None = None
    archived: bool = False
    installed: bool = False
    installed_version: str | None = None
    update_available: bool = False


class RegistryListResponse(BaseModel):
    pipelines: list[RegistryPipelineItem]
    total: int
    last_refreshed_at: datetime | None = None


class RegistryVersion(BaseModel):
    tag_name: str
    published_at: str | None = None
    has_schema: bool = False


class RegistryVersionsResponse(BaseModel):
    name: str
    versions: list[RegistryVersion]


class RegistryInstallRequest(BaseModel):
    version: str


class RegistryRefreshResponse(BaseModel):
    fetched: int
    archived: int
    error: str | None = None
    last_refreshed_at: datetime | None = None
