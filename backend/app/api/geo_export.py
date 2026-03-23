"""GEO export API endpoint."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.services.audit_service import log_action
from app.services.geo.geo_export_service import GeoExportService
from app.services.geo.superseries_export_service import SuperSeriesExportService

router = APIRouter(tags=["geo-export"])


@router.post("/api/experiments/{experiment_id}/export/geo")
async def export_geo(
    experiment_id: int,
    pipeline_run_id: int | None = None,
    qc_status_filter: str = "exclude_failed",
    validate_only: bool = False,
    current_user: dict = require_permission("files", "download"),
    session: AsyncSession = Depends(get_session),
):
    """Export experiment data as a GEO submission package.

    If validate_only=true, returns a ValidationReport as JSON.
    Otherwise, returns a ZIP file download.
    """
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    try:
        if validate_only:
            report = await GeoExportService.validate(session, experiment_id, org_id, pipeline_run_id, qc_status_filter)

            await log_action(
                session,
                user_id=user_id,
                entity_type="geo_export",
                entity_id=experiment_id,
                action="validated",
                details={
                    "pipeline_run_id": pipeline_run_id,
                    "qc_status_filter": qc_status_filter,
                    "missing_required": report.summary.missing_required,
                },
            )
            await session.commit()

            return report.model_dump()

        zip_bytes, filename = await GeoExportService.export(
            session, experiment_id, org_id, pipeline_run_id, qc_status_filter
        )

        await log_action(
            session,
            user_id=user_id,
            entity_type="geo_export",
            entity_id=experiment_id,
            action="exported",
            details={
                "pipeline_run_id": pipeline_run_id,
                "qc_status_filter": qc_status_filter,
                "filename": filename,
            },
        )
        await session.commit()

        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/api/projects/{project_id}/export/geo")
async def export_project_geo(
    project_id: int,
    pipeline_run_ids: dict[int, int] | None = None,
    qc_status_filter: str = "exclude_failed",
    exclude_unclaimed: bool = True,
    validate_only: bool = False,
    current_user: dict = require_permission("files", "download"),
    session: AsyncSession = Depends(get_session),
):
    """Export project data as a GEO SuperSeries package.

    If validate_only=true, returns cross-experiment validation results.
    Otherwise, returns a SuperSeries ZIP file download.
    """
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    try:
        if validate_only:
            validation = await SuperSeriesExportService.validate_cross_experiment(
                session, project_id, org_id, exclude_unclaimed=exclude_unclaimed
            )
            return validation.to_dict()

        zip_bytes, filename = await SuperSeriesExportService.export(
            session,
            project_id,
            org_id,
            pipeline_run_ids=pipeline_run_ids,
            qc_status_filter=qc_status_filter,
            exclude_unclaimed=exclude_unclaimed,
        )

        await log_action(
            session,
            user_id=user_id,
            entity_type="geo_superseries_export",
            entity_id=project_id,
            action="exported",
            details={
                "qc_status_filter": qc_status_filter,
                "exclude_unclaimed": exclude_unclaimed,
                "filename": filename,
            },
        )
        await session.commit()

        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except ValueError as e:
        raise HTTPException(404, str(e))
