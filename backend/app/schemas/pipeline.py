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
