from datetime import datetime

from pydantic import BaseModel, field_validator


class SampleBatchSummary(BaseModel):
    id: int
    name: str

    model_config = {"from_attributes": True}


class SequencingBatchSummary(BaseModel):
    id: int
    code: str

    model_config = {"from_attributes": True}


class SampleCustomFieldValue(BaseModel):
    field_name: str
    field_value: str


class SampleCustomFieldResponse(BaseModel):
    id: int
    field_name: str
    field_value: str | None

    model_config = {"from_attributes": True}


class SampleCreate(BaseModel):
    sample_id_external: str | None = None
    organism: str | None = None
    tissue_type: str | None = None
    donor_source: str | None = None
    treatment_condition: str | None = None
    chemistry_version: str | None = None
    sample_batch_code: str | None = None
    sequencing_batch_code: str | None = None
    viability_pct: float | None = None
    cell_count: int | None = None
    prep_notes: str | None = None
    molecule_type: str | None = None
    library_prep_method: str | None = None
    library_layout: str | None = None
    qc_status: str | None = None
    qc_notes: str | None = None
    parent_sample_id: int | None = None
    collection_timestamp: datetime | None = None
    collection_method: str | None = None
    custom_fields: list[SampleCustomFieldValue] | None = None

    @field_validator("viability_pct")
    @classmethod
    def validate_viability(cls, v: float | None) -> float | None:
        if v is not None and (v < 0 or v > 100):
            raise ValueError("viability_pct must be between 0 and 100")
        return v

    @field_validator("qc_status")
    @classmethod
    def validate_qc_status(cls, v: str | None) -> str | None:
        if v is not None and v not in ("pass", "warning", "fail"):
            raise ValueError("qc_status must be 'pass', 'warning', or 'fail'")
        return v


class SampleUpdate(BaseModel):
    sample_id_external: str | None = None
    organism: str | None = None
    tissue_type: str | None = None
    donor_source: str | None = None
    treatment_condition: str | None = None
    chemistry_version: str | None = None
    sample_batch_code: str | None = None
    sequencing_batch_code: str | None = None
    viability_pct: float | None = None
    cell_count: int | None = None
    prep_notes: str | None = None
    molecule_type: str | None = None
    library_prep_method: str | None = None
    library_layout: str | None = None
    parent_sample_id: int | None = None
    collection_timestamp: datetime | None = None
    collection_method: str | None = None
    custom_fields: list[SampleCustomFieldValue] | None = None

    @field_validator("viability_pct")
    @classmethod
    def validate_viability(cls, v: float | None) -> float | None:
        if v is not None and (v < 0 or v > 100):
            raise ValueError("viability_pct must be between 0 and 100")
        return v


class SampleBulkCreate(BaseModel):
    samples: list[SampleCreate]


class SampleBulkUpdate(BaseModel):
    sample_ids: list[int]
    update: SampleUpdate


class SampleQCUpdate(BaseModel):
    qc_status: str
    qc_notes: str | None = None

    @field_validator("qc_status")
    @classmethod
    def validate_qc_status(cls, v: str) -> str:
        if v not in ("pass", "warning", "fail"):
            raise ValueError("qc_status must be 'pass', 'warning', or 'fail'")
        return v


class SampleStatusUpdate(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        from app.models.sample import SAMPLE_STATUSES

        if v not in SAMPLE_STATUSES:
            raise ValueError(f"Invalid status '{v}'. Must be one of: {', '.join(SAMPLE_STATUSES)}")
        return v


class SampleResponse(BaseModel):
    id: int
    sample_id_external: str | None
    organism: str | None
    tissue_type: str | None
    donor_source: str | None
    treatment_condition: str | None
    chemistry_version: str | None
    sample_batch: SampleBatchSummary | None = None
    sequencing_batch: SequencingBatchSummary | None = None
    viability_pct: float | None
    cell_count: int | None
    prep_notes: str | None
    molecule_type: str | None = None
    library_prep_method: str | None = None
    library_layout: str | None = None
    qc_status: str | None
    qc_notes: str | None
    parent_sample_id: int | None = None
    collection_timestamp: datetime | None = None
    collection_method: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime
    custom_fields: list[SampleCustomFieldResponse] = []

    model_config = {"from_attributes": True}
