"""Pydantic schemas for work node API endpoints."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class UserSummary(BaseModel):
    id: int
    name: str | None = None
    email: str

    model_config = {"from_attributes": True}


class ProjectSummary(BaseModel):
    id: int
    name: str

    model_config = {"from_attributes": True}


class WorkNodeLaunchRequest(BaseModel):
    # Both scopes are optional, matching notebooks: a work node can be scoped to a
    # project, to a standalone experiment (which may have no project), or neither.
    # project_id is metadata on the session; experiment_id ties outputs to the
    # experiment in the same way notebook sessions do.
    project_id: int | None = None
    experiment_id: int | None = None
    environment_version_id: int
    machine_type: str
    input_file_ids: list[int] | None = None
    github_repo_ids: list[int] | None = None


class WorkNodeResponse(BaseModel):
    id: int
    session_type: str
    user: UserSummary | None = None
    project_id: int | None = None
    project: ProjectSummary | None = None
    environment_version_id: int | None = None
    machine_type: str | None = None
    input_file_ids: list[int] | None = None
    resource_profile: str
    cpu_cores: int
    memory_gb: int
    requested_disk_gb: int | None = None
    status: str
    failure_reason: str | None = None
    failure_message: str | None = None
    access_url: str | None = None
    gce_instance_name: str | None = None
    gce_zone: str | None = None
    github_repo_ids: list[int] | None = None
    heartbeat_at: datetime | None = None
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class WorkNodeListResponse(BaseModel):
    sessions: list[WorkNodeResponse]
    total: int


class MachineTypeResponse(BaseModel):
    name: str
    category: str
    cpu: int
    memory_gb: int
    gpu: str | None = None
    description: str


class WorkNodeSettings(BaseModel):
    max_nodes_per_user: int = Field(default=2, ge=1, le=50)
    idle_timeout_hours: int = Field(default=24, ge=1, le=720)
    # Boot disk for the work-node VM. pd-ssd and pd-balanced both count toward
    # the regional SSD_TOTAL_GB quota; pd-standard uses the separate HDD quota.
    boot_disk_gb: int = Field(default=100, ge=20, le=1000)
    boot_disk_type: Literal["pd-ssd", "pd-balanced", "pd-standard"] = "pd-ssd"
