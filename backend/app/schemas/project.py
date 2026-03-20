from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ProjectCreate(BaseModel):
    name: str
    description: str | None = None
    hypothesis: str | None = None
    sample_ids: list[int] | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    hypothesis: str | None = None
    status: Literal["active", "archived", "complete"] | None = None


class ProjectSamplesAdd(BaseModel):
    sample_ids: list[int]
    notes: str | None = None


class ProjectSampleResponse(BaseModel):
    sample_id: int
    sample_id_external: str | None = None
    organism: str | None = None
    tissue_type: str | None = None
    qc_status: str | None = None
    added_by: str | None = None
    added_at: datetime | None = None
    notes: str | None = None


class ProjectSampleGroup(BaseModel):
    experiment_id: int
    experiment_name: str
    samples: list[ProjectSampleResponse]


class PipelineRunSummary(BaseModel):
    id: int
    pipeline_name: str
    pipeline_version: str | None = None
    status: str
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class ProjectResponse(BaseModel):
    id: int
    name: str
    description: str | None = None
    hypothesis: str | None = None
    status: str | None = None
    owner_user_id: int | None = None
    owner_name: str | None = None
    sample_count: int = 0
    experiment_count: int = 0
    pipeline_run_count: int = 0
    snapshot_count: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


class ExperimentSummary(BaseModel):
    id: int
    name: str
    status: str
    sample_count: int = 0
    created_at: datetime | None = None


class ProjectDetailResponse(ProjectResponse):
    samples: list[ProjectSampleGroup] = []
    experiments: list[ExperimentSummary] = []
    pipeline_runs: list[PipelineRunSummary] = []


class ProjectListResponse(BaseModel):
    projects: list[ProjectResponse]
    total: int
