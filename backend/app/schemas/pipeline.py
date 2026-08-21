from datetime import datetime

from pydantic import BaseModel


class SamplesheetInputSpec(BaseModel):
    """One samplesheet column the user must supply at launch.

    Only columns whose value is constant across the whole run appear here, so a
    single field collects them. ``allowed_values`` comes from the pipeline's own
    schema, so the options offered cannot drift from what it accepts; empty means
    unconstrained free text.
    """

    name: str
    parameter: str
    required: bool = True
    allowed_values: list[str] = []


class PipelineCatalogResponse(BaseModel):
    id: int
    pipeline_key: str
    name: str
    description: str | None = None
    source_type: str
    source_url: str | None = None
    version: str | None = None
    parameter_schema: dict | None = None
    # Required samplesheet columns the user must answer, from the pipeline's
    # assets/schema_input.json. Distinct from parameter_schema, which carries
    # nextflow_schema.json's pipeline PARAMETERS; a samplesheet column such as
    # instrument_platform appears in neither the params nor any existing form.
    samplesheet_inputs: list[SamplesheetInputSpec] = []
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
