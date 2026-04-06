from datetime import date, datetime

from pydantic import BaseModel

from app.schemas.experiment import UserSummary


class SampleBatchCreate(BaseModel):
    name: str
    prep_date: date | None = None
    operator_user_id: int | None = None
    sequencer_run_id: str | None = None
    instrument_model: str | None = None
    instrument_platform: str | None = None
    quality_score_encoding: str | None = None
    notes: str | None = None


class SampleBatchUpdate(BaseModel):
    name: str | None = None
    prep_date: date | None = None
    operator_user_id: int | None = None
    sequencer_run_id: str | None = None
    instrument_model: str | None = None
    instrument_platform: str | None = None
    quality_score_encoding: str | None = None
    notes: str | None = None


class SampleBatchAssignSamples(BaseModel):
    sample_ids: list[int]


class SampleBatchResponse(BaseModel):
    id: int
    name: str
    prep_date: date | None
    operator: UserSummary | None = None
    sequencer_run_id: str | None = None
    instrument_model: str | None = None
    instrument_platform: str | None = None
    quality_score_encoding: str | None = None
    notes: str | None
    sample_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
