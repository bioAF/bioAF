from typing import Literal
from datetime import datetime

from pydantic import BaseModel

from app.schemas.file import FileResponse


class PlotArchiveResponse(BaseModel):
    id: int
    title: str | None
    file: FileResponse | None = None
    experiment_id: int | None
    experiment_name: str | None = None
    project_name: str | None = None
    pipeline_run_id: int | None
    pipeline_run_name: str | None = None
    notebook_session_id: int | None
    notebook_session_type: str | None = None
    source_type: str | None = None
    tags: list[str] = []
    thumbnail_url: str | None = None
    indexed_at: datetime

    model_config = {"from_attributes": True}


class PlotScanHealth(BaseModel):
    # "ok", or why new plots are not being added: missing, no_access, not_confirmed, temporary.
    status: Literal["ok", "missing", "no_access", "not_confirmed", "temporary"]
    last_success_at: datetime | None = None
    last_attempt_at: datetime | None = None
    next_attempt_at: datetime | None = None


class PlotArchiveListResponse(BaseModel):
    plots: list[PlotArchiveResponse]
    total: int
    page: int
    page_size: int
    # Whether new plots are being added from the results store (mockup M13).
    scan_health: PlotScanHealth | None = None


class PlotUpdateRequest(BaseModel):
    title: str | None = None
    tags: list[str] | None = None
