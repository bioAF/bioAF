from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.schemas.experiment import UserSummary
from app.schemas.file import FileResponse
from app.schemas.plot_archive import PlotArchiveListResponse, PlotArchiveResponse, PlotUpdateRequest
from app.services.plot_archive_service import PlotArchiveService

router = APIRouter(prefix="/api/plots", tags=["plots"])


def _plot_response(p) -> PlotArchiveResponse:
    file_resp = None
    if p.file:
        file_resp = FileResponse(
            id=p.file.id,
            filename=p.file.filename,
            gcs_uri=p.file.gcs_uri,
            size_bytes=p.file.size_bytes,
            md5_checksum=p.file.md5_checksum,
            file_type=p.file.file_type,
            tags=p.file.tags_json if isinstance(p.file.tags_json, list) else [],
            uploader=UserSummary(id=p.file.uploader.id, name=p.file.uploader.name, email=p.file.uploader.email)
            if p.file and p.file.uploader
            else None,
            upload_timestamp=p.file.upload_timestamp,
            created_at=p.file.created_at,
        )
    return PlotArchiveResponse(
        id=p.id,
        title=p.title,
        file=file_resp,
        experiment_id=p.experiment_id,
        pipeline_run_id=p.pipeline_run_id,
        notebook_session_id=p.notebook_session_id,
        tags=p.tags_json if isinstance(p.tags_json, list) else [],
        thumbnail_url=p.thumbnail_gcs_uri,
        indexed_at=p.indexed_at,
    )


@router.get("", response_model=PlotArchiveListResponse)
async def search_plots(
    request: Request,
    experiment_id: int | None = None,
    pipeline_run_id: int | None = None,
    query: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = 1,
    page_size: int = 25,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    plots, total = await PlotArchiveService.search_plots(
        session,
        org_id,
        experiment_id=experiment_id,
        pipeline_run_id=pipeline_run_id,
        query=query,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )
    return PlotArchiveListResponse(
        plots=[_plot_response(p) for p in plots],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{plot_id}", response_model=PlotArchiveResponse)
async def get_plot(
    plot_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    plot = await PlotArchiveService.get_plot(session, org_id, plot_id)
    if not plot:
        raise HTTPException(404, "Plot not found")
    return _plot_response(plot)


@router.get("/{plot_id}/thumbnail")
async def get_thumbnail(
    plot_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    plot = await PlotArchiveService.get_plot(session, org_id, plot_id)
    if not plot:
        raise HTTPException(404, "Plot not found")

    if plot.thumbnail_gcs_uri:
        return {"thumbnail_url": plot.thumbnail_gcs_uri}
    elif plot.file:
        return {"thumbnail_url": plot.file.gcs_uri}
    return {"thumbnail_url": None}


@router.post("/backfill")
async def backfill_plot_metadata(
    current_user: dict = require_permission("experiments", "create"),
    session: AsyncSession = Depends(get_session),
):
    updated = await PlotArchiveService.backfill_metadata(session)
    return {"updated": updated}


@router.patch("/{plot_id}", response_model=PlotArchiveResponse)
async def update_plot(
    plot_id: int,
    body: PlotUpdateRequest,
    current_user: dict = require_permission("experiments", "edit"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    plot = await PlotArchiveService.update_plot(
        session,
        org_id,
        plot_id,
        user_id,
        title=body.title,
        tags=body.tags,
    )
    if not plot:
        raise HTTPException(404, "Plot not found")
    await session.commit()
    plot = await PlotArchiveService.get_plot(session, org_id, plot_id)
    return _plot_response(plot)
