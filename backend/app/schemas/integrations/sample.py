from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.integrations.common import CustomFieldIn, CustomFieldOut


class SampleCreate(BaseModel):
    sample_id_external: str | None = Field(None, max_length=255)
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
    custom_fields: list[CustomFieldIn] | None = None
    # qc_status / status intentionally NOT accepted; QC has its own flow


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
    custom_fields: list[CustomFieldIn] | None = None


class SampleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sample_id_external: str | None
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
    qc_status: str | None
    status: str
    created_at: datetime
    custom_fields: list[CustomFieldOut] = []


class SampleListOut(BaseModel):
    items: list[SampleOut]
    next_cursor: str | None = None
