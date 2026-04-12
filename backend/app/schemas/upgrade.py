from datetime import datetime

from pydantic import BaseModel


class VersionInfo(BaseModel):
    current_version: str
    app_name: str = "bioAF"
    build_date: str | None = None
    commit_hash: str | None = None


class UpdateCheckResponse(BaseModel):
    current_version: str
    latest_version: str
    update_available: bool
    changelog: str | None = None
    release_url: str | None = None


class UpgradeHistoryItem(BaseModel):
    id: int
    from_version: str
    to_version: str
    status: str
    started_by_user_id: int
    started_at: datetime
    completed_at: datetime | None = None
    notes: str | None = None

    model_config = {"from_attributes": True}


class UpgradeHistoryListResponse(BaseModel):
    upgrades: list[UpgradeHistoryItem]
    total: int


class StartUpgradeRequest(BaseModel):
    target_version: str


class StartUpgradeResponse(BaseModel):
    upgrade_id: int
    status: str
    from_version: str
    to_version: str
    terraform_plan: dict | None = None


class ConfirmUpgradeResponse(BaseModel):
    upgrade_id: int
    status: str
    message: str


class RollbackResponse(BaseModel):
    upgrade_id: int
    status: str
    message: str


class ExecuteUpgradeRequest(BaseModel):
    target_version: str


class ExecuteUpgradeResponse(BaseModel):
    upgrade_id: int
    status: str
    from_version: str
    to_version: str


class UpdateStatusResponse(BaseModel):
    status: str
    from_version: str | None = None
    to_version: str | None = None
    step: str | None = None
    error: str | None = None
