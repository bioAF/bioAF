"""Pydantic schemas for work node API endpoints."""

from datetime import datetime

from pydantic import BaseModel, Field


class UserSummary(BaseModel):
    id: int
    name: str | None = None
    email: str

    model_config = {"from_attributes": True}


class WorkNodeLaunchRequest(BaseModel):
    project_id: int
    environment_version_id: int
    machine_type: str
    data_mount_paths: list[str] | None = None


class WorkNodeResponse(BaseModel):
    id: int
    session_type: str
    user: UserSummary | None = None
    project_id: int | None = None
    environment_version_id: int | None = None
    machine_type: str | None = None
    data_mount_paths: list[str] | None = None
    resource_profile: str
    cpu_cores: int
    memory_gb: int
    status: str
    access_url: str | None = None
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
