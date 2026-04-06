from datetime import datetime

from pydantic import BaseModel


class SequencingBatchCreate(BaseModel):
    name: str
    code: str
    instrument_model: str | None = None
    instrument_platform: str | None = None
    quality_score_encoding: str | None = None
    sequencer_run_id: str | None = None
    notes: str | None = None


class SequencingBatchUpdate(BaseModel):
    name: str | None = None
    code: str | None = None
    status: str | None = None
    instrument_model: str | None = None
    instrument_platform: str | None = None
    quality_score_encoding: str | None = None
    sequencer_run_id: str | None = None
    expected_file_count: int | None = None
    ingested_file_count: int | None = None
    notes: str | None = None


class ManifestEntryResponse(BaseModel):
    id: int
    expected_filename: str
    expected_md5: str
    resolved_sample_id: int | None = None
    resolved_experiment_id: int | None = None
    resolved_project_id: int | None = None
    file_id: int | None = None
    status: str
    last_check_at: datetime | None = None
    retry_count: int = 0
    error_message: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class SequencingBatchResponse(BaseModel):
    id: int
    organization_id: int
    name: str
    code: str
    status: str
    instrument_model: str | None = None
    instrument_platform: str | None = None
    quality_score_encoding: str | None = None
    sequencer_run_id: str | None = None
    manifest_received_at: datetime | None = None
    expected_file_count: int | None = None
    ingested_file_count: int = 0
    notes: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SequencingBatchDetailResponse(SequencingBatchResponse):
    manifest_entries: list[ManifestEntryResponse] = []
