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


class PlotArchiveListResponse(BaseModel):
    plots: list[PlotArchiveResponse]
    total: int
    page: int
    page_size: int


class PlotUpdateRequest(BaseModel):
    title: str | None = None
    tags: list[str] | None = None
