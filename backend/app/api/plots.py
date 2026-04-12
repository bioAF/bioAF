from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
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
            storage_deleted=p.file.storage_deleted,
            upload_timestamp=p.file.upload_timestamp,
            created_at=p.file.created_at,
        )
    # Derive source type from associations
    source_type = None
    if p.file and p.file.source_type:
        source_type = p.file.source_type
    elif p.notebook_session_id:
        session_type = getattr(p.notebook_session, "session_type", None) if p.notebook_session else None
        if session_type == "cellxgene":
            source_type = "cellxgene"
        else:
            source_type = "notebook"
    elif p.pipeline_run_id:
        source_type = "pipeline"

    return PlotArchiveResponse(
        id=p.id,
        title=p.title,
        file=file_resp,
        experiment_id=p.experiment_id,
        experiment_name=p.experiment.name if p.experiment else None,
        project_name=p.experiment.project.name if p.experiment and p.experiment.project else None,
        pipeline_run_id=p.pipeline_run_id,
        pipeline_run_name=p.pipeline_run.pipeline_name if p.pipeline_run else None,
        notebook_session_id=p.notebook_session_id,
        notebook_session_type=p.notebook_session.session_type if p.notebook_session else None,
        source_type=source_type,
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


@router.get("/{plot_id}/thumbnail/content")
async def get_thumbnail_content(
    plot_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Serve thumbnail PNG bytes for inline display in <img> tags."""
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    plot = await PlotArchiveService.get_plot(session, org_id, plot_id)
    if not plot:
        raise HTTPException(404, "Plot not found")
    if not plot.thumbnail_gcs_uri:
        raise HTTPException(404, "No thumbnail available")

    try:
        from google.cloud import storage as gcs_storage

        from app.services.gcs_storage import GcsStorageService

        credentials = await GcsStorageService.get_credentials(session)
        client = gcs_storage.Client(credentials=credentials)
        parts = plot.thumbnail_gcs_uri.replace("gs://", "").split("/", 1)
        bucket = client.bucket(parts[0])
        blob = bucket.blob(parts[1])
        data = blob.download_as_bytes()

        return Response(
            content=data,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=3600"},
        )
    except Exception:
        raise HTTPException(502, "Could not fetch thumbnail content")


@router.post("/backfill")
async def backfill_plot_metadata(
    current_user: dict = require_permission("experiments", "create"),
    session: AsyncSession = Depends(get_session),
):
    metadata_updated = await PlotArchiveService.backfill_metadata(session)
    thumbnails_generated = await PlotArchiveService.backfill_thumbnails(session)
    return {"metadata_updated": metadata_updated, "thumbnails_generated": thumbnails_generated}


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
