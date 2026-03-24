from datetime import date, datetime

from pydantic import BaseModel, field_validator

from app.models.experiment import EXPERIMENT_STATUSES


class UserSummary(BaseModel):
    id: int
    name: str | None = None
    email: str

    model_config = {"from_attributes": True}


class CustomFieldValue(BaseModel):
    field_name: str
    field_value: str
    field_type: str = "string"


class FieldDefaultValue(BaseModel):
    field_name: str
    default_value: str | None = None
    is_required: bool | None = None

    @field_validator("field_name")
    @classmethod
    def validate_field_name(cls, v: str) -> str:
        from app.models.experiment_field_default import DEFAULTABLE_SAMPLE_FIELDS

        if v not in DEFAULTABLE_SAMPLE_FIELDS:
            raise ValueError(f"Invalid field '{v}'. Must be one of: {', '.join(DEFAULTABLE_SAMPLE_FIELDS)}")
        return v


class FieldDefaultResponse(BaseModel):
    id: int
    field_name: str
    default_value: str | None
    is_required: bool | None

    model_config = {"from_attributes": True}


class CustomFieldResponse(BaseModel):
    id: int
    field_name: str
    field_value: str | None
    field_type: str

    model_config = {"from_attributes": True}


class ExperimentCreate(BaseModel):
    name: str
    project_id: int | None = None
    template_id: int | None = None
    hypothesis: str | None = None
    description: str | None = None
    start_date: date | None = None
    expected_sample_count: int | None = None
    custom_fields: list[CustomFieldValue] | None = None
    field_defaults: list[FieldDefaultValue] | None = None
    design_type: str | None = None
    protocol_version: str | None = None
    variables_json: dict | None = None


class ExperimentUpdate(BaseModel):
    name: str | None = None
    hypothesis: str | None = None
    description: str | None = None
    start_date: date | None = None
    expected_sample_count: int | None = None
    field_defaults: list[FieldDefaultValue] | None = None
    design_type: str | None = None
    protocol_version: str | None = None
    variables_json: dict | None = None


class ExperimentStatusUpdate(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        if v not in EXPERIMENT_STATUSES:
            raise ValueError(f"Invalid status '{v}'. Must be one of: {', '.join(EXPERIMENT_STATUSES)}")
        return v


class ProjectSummary(BaseModel):
    id: int
    name: str

    model_config = {"from_attributes": True}


class ExperimentResponse(BaseModel):
    id: int
    name: str
    project: ProjectSummary | None = None
    template_id: int | None = None
    template_name: str | None = None
    hypothesis: str | None
    description: str | None
    status: str
    start_date: date | None
    expected_sample_count: int | None
    owner: UserSummary | None = None
    design_type: str | None = None
    protocol_version: str | None = None
    variables_json: dict | None = None
    sample_count: int = 0
    batch_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ExperimentListResponse(BaseModel):
    experiments: list[ExperimentResponse]
    total: int
    page: int
    page_size: int


class SampleResponseBrief(BaseModel):
    id: int
    sample_id_external: str | None
    organism: str | None
    tissue_type: str | None
    molecule_type: str | None = None
    library_prep_method: str | None = None
    library_layout: str | None = None
    qc_status: str | None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class BatchResponseBrief(BaseModel):
    id: int
    name: str
    instrument_model: str | None = None
    instrument_platform: str | None = None
    sample_count: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


class ExperimentDetailResponse(ExperimentResponse):
    samples: list[SampleResponseBrief] = []
    batches: list[BatchResponseBrief] = []
    custom_fields: list[CustomFieldResponse] = []
    field_defaults: list[FieldDefaultResponse] = []
    audit_trail_count: int = 0
