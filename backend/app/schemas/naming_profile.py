from datetime import datetime
from typing import Literal

from pydantic import BaseModel, field_validator


SEGMENT_FIELDS = [
    "date",
    "project_code",
    "experiment_code",
    "sample_id",
    "sample_index",
    "data_type",
    "analysis_type",
    "researcher_initials",
    "version",
    "batch_id",
    "organism",
    "ignore",
]


class SegmentDefinition(BaseModel):
    position: int
    field: Literal[
        "date",
        "project_code",
        "experiment_code",
        "sample_id",
        "sample_index",
        "data_type",
        "analysis_type",
        "researcher_initials",
        "version",
        "batch_id",
        "organism",
        "ignore",
    ]
    format: str | None = None
    maps_to: str | None = None
    required: bool = True

    @field_validator("position")
    @classmethod
    def validate_position(cls, v: int) -> int:
        if v < 0:
            raise ValueError("position must be non-negative")
        return v


class NamingProfileCreate(BaseModel):
    name: str
    description: str | None = None
    delimiter: str = "_"
    strip_extension: bool = True
    segments: list[SegmentDefinition]
    project_code_mappings: dict[str, str] = {}
    experiment_code_mappings: dict[str, str] = {}

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must not be empty")
        return v

    @field_validator("segments")
    @classmethod
    def validate_segments(cls, v: list[SegmentDefinition]) -> list[SegmentDefinition]:
        if len(v) == 0:
            raise ValueError("at least one segment is required")
        return v


class NamingProfileUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    delimiter: str | None = None
    strip_extension: bool | None = None
    segments: list[SegmentDefinition] | None = None
    project_code_mappings: dict[str, str] | None = None
    experiment_code_mappings: dict[str, str] | None = None


class NamingProfileResponse(BaseModel):
    id: int
    organization_id: int
    name: str
    description: str | None
    delimiter: str
    strip_extension: bool
    segments: list[SegmentDefinition]
    project_code_mappings: dict[str, str]
    experiment_code_mappings: dict[str, str]
    status: str
    created_by: int
    created_at: datetime
    updated_at: datetime
    match_count_30d: int | None = None

    model_config = {"from_attributes": True}


class NamingProfileTestRequest(BaseModel):
    filenames: list[str]

    @field_validator("filenames")
    @classmethod
    def validate_filenames(cls, v: list[str]) -> list[str]:
        if len(v) == 0:
            raise ValueError("at least one filename is required")
        return v


class NamingProfileTestResult(BaseModel):
    filename: str
    matched_profile_id: int | None = None
    matched_profile_name: str | None = None
    match_status: Literal["matched", "multiple_matches", "unmatched"]
    parsed_segments: dict[str, str] | None = None
    resolved_project: str | None = None
    resolved_experiment: str | None = None
    resolved_sample: str | None = None
    candidate_profile_ids: list[int] | None = None
