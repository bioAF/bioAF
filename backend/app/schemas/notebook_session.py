from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ExperimentSummary(BaseModel):
    id: int
    name: str

    model_config = {"from_attributes": True}


class UserSummary(BaseModel):
    id: int
    name: str | None = None
    email: str

    model_config = {"from_attributes": True}


class SessionLaunchRequest(BaseModel):
    session_type: Literal["jupyter", "rstudio"]
    resource_profile: Literal["small", "medium", "large"]
    experiment_id: int | None = None
    input_file_ids: list[int] = []


class SessionResponse(BaseModel):
    id: int
    session_type: str
    user: UserSummary | None = None
    experiment: ExperimentSummary | None = None
    resource_profile: str
    cpu_cores: int
    memory_gb: int
    status: str
    idle_since: datetime | None = None
    proxy_url: str | None = None
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    created_at: datetime
    git_branch_name: str | None = None
    git_commit_hash: str | None = None
    environment_version_id: int | None = None

    model_config = {"from_attributes": True}


class SessionListResponse(BaseModel):
    sessions: list[SessionResponse]
    total: int
