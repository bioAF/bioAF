from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.integrations.common import CustomFieldIn, CustomFieldOut


class SampleCreate(BaseModel):
    external_id: str = Field(..., min_length=1, max_length=255)
    experiment_id: int
    organism: str | None = None
    tissue_type: str | None = None
    donor_source: str | None = None
    treatment_condition: str | None = None
    chemistry_version: str | None = None
    cell_count: int | None = None
    prep_notes: str | None = None
    molecule_type: str | None = None
    library_prep_method: str | None = None
    assay: str | None = None
    custom_fields: list[CustomFieldIn] | None = None
    # qc_status / status intentionally NOT accepted; QC has its own flow

    @field_validator("assay")
    @classmethod
    def _validate_assay(cls, v: str | None) -> str | None:
        from app.models.sample import SAMPLE_ASSAYS

        if v is not None and v not in SAMPLE_ASSAYS:
            raise ValueError(f"assay must be one of: {', '.join(SAMPLE_ASSAYS)}")
        return v


class SampleUpdate(BaseModel):
    organism: str | None = None
    tissue_type: str | None = None
    donor_source: str | None = None
    treatment_condition: str | None = None
    chemistry_version: str | None = None
    cell_count: int | None = None
    prep_notes: str | None = None
    molecule_type: str | None = None
    library_prep_method: str | None = None
    assay: str | None = None
    custom_fields: list[CustomFieldIn] | None = None

    @field_validator("assay")
    @classmethod
    def _validate_assay(cls, v: str | None) -> str | None:
        from app.models.sample import SAMPLE_ASSAYS

        if v is not None and v not in SAMPLE_ASSAYS:
            raise ValueError(f"assay must be one of: {', '.join(SAMPLE_ASSAYS)}")
        return v


class SampleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    external_id: str | None
    experiment_id: int
    organism: str | None
    tissue_type: str | None
    donor_source: str | None
    treatment_condition: str | None
    chemistry_version: str | None
    cell_count: int | None
    prep_notes: str | None
    molecule_type: str | None
    library_prep_method: str | None
    assay: str | None = None
    qc_status: str | None
    status: str
    created_at: datetime
    custom_fields: list[CustomFieldOut] = []


class SampleListOut(BaseModel):
    items: list[SampleOut]
    next_cursor: str | None = None
