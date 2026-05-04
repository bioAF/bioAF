from datetime import datetime

from pydantic import BaseModel, Field, model_validator

# Match REFERENCE_CATEGORIES from app.models.reference_dataset, plus 'any'
# meaning "match any category".
VALID_REFERENCE_CATEGORIES = {
    "genome",
    "annotation",
    "index",
    "atlas",
    "markers",
    "other",
    "any",
}


# --- Request schemas ---


class CustomPipelineCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None


class CustomPipelineUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None


class CustomPipelineVariableDefinition(BaseModel):
    variable_name: str = Field(..., min_length=1, max_length=255)
    default_value: str | None = None
    variable_type: str = Field("string", pattern="^(string|number|boolean|reference)$")
    reference_category: str | None = None
    is_required: bool = False

    @model_validator(mode="after")
    def _validate_reference_category(self) -> "CustomPipelineVariableDefinition":
        if self.variable_type == "reference":
            if not self.reference_category:
                raise ValueError("reference_category is required when variable_type='reference'")
            if self.reference_category not in VALID_REFERENCE_CATEGORIES:
                raise ValueError(f"reference_category must be one of: {', '.join(sorted(VALID_REFERENCE_CATEGORIES))}")
        elif self.reference_category is not None:
            # Silently drop reference_category for non-reference types so we
            # never persist nonsense.
            self.reference_category = None
        return self


class CustomPipelineVariableValue(BaseModel):
    variable_name: str
    variable_value: str


class CustomPipelineVersionCreateRequest(BaseModel):
    code_source_type: str = Field(..., pattern="^(github_repo|code_blob|inline)$")
    code_content: str | None = None
    github_repo_id: int | None = None
    entrypoint_command: str = Field(..., min_length=1)
    environment_version_id: int
    cpu_request: str = "2"
    memory_request: str = "8Gi"
    log_file_path: str | None = None
    variables: list[CustomPipelineVariableDefinition] = []
    qc_template: str | None = None
    qc_config_json: dict | None = None


class CustomPipelineLaunchRequest(BaseModel):
    version_id: int
    experiment_id: int | None = None
    project_id: int | None = None
    input_file_ids: list[int]
    variables: list[CustomPipelineVariableValue] = []


# --- Response schemas ---


class CustomPipelineVariableResponse(BaseModel):
    id: int
    variable_name: str
    default_value: str | None = None
    variable_type: str
    reference_category: str | None = None
    is_required: bool

    model_config = {"from_attributes": True}


class CustomPipelineVersionResponse(BaseModel):
    id: int
    version_number: int
    code_source_type: str
    github_repo_id: int | None = None
    code_content: str | None = None
    entrypoint_command: str
    environment_version_id: int
    cpu_request: str
    memory_request: str
    log_file_path: str | None = None
    version_trigger: str
    status: str
    created_by_user_id: int
    created_at: datetime
    variables: list[CustomPipelineVariableResponse] = []
    qc_template: str | None = None
    qc_config_json: dict | None = None

    model_config = {"from_attributes": True}


class CustomPipelineResponse(BaseModel):
    id: int
    name: str
    description: str | None = None
    pipeline_key: str
    created_by_user_id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CustomPipelineDetailResponse(BaseModel):
    id: int
    name: str
    description: str | None = None
    pipeline_key: str
    created_by_user_id: int
    created_at: datetime
    updated_at: datetime
    versions: list[CustomPipelineVersionResponse] = []

    model_config = {"from_attributes": True}


class CustomPipelineEnvironmentSummary(BaseModel):
    environment_id: int
    environment_name: str
    version_id: int
    version_number: int
    build_number: int
    image_uri: str | None = None


class CustomPipelineRunOverview(BaseModel):
    """Summary used by the run detail page for custom pipeline runs."""

    pipeline_id: int
    pipeline_name: str
    pipeline_key: str
    version_id: int
    version_number: int
    code_source_type: str
    github_repo_id: int | None = None
    entrypoint_command: str
    cpu_request: str
    memory_request: str
    log_file_path: str | None = None
    environment: CustomPipelineEnvironmentSummary | None = None
    variables: list[CustomPipelineVariableResponse] = []
