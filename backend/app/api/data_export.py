"""Data export API endpoints for experiments and projects."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.services.audit_service import log_action
from app.services import export_service

router = APIRouter(tags=["data-export"])


class ExportRequest(BaseModel):
    delivery_method: str  # "direct" or "gcs"
    include_fastq: bool = False
    include_provenance: bool = True


# ---------------------------------------------------------------------------
# Estimate endpoints
# ---------------------------------------------------------------------------


@router.get("/api/experiments/{experiment_id}/export/estimate")
async def estimate_experiment_export(
    experiment_id: int,
    include_fastq: bool = False,
    current_user: dict = require_permission("files", "download"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    result = await export_service.estimate_experiment_export(session, experiment_id, org_id, include_fastq)
    if result is None:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return result


@router.get("/api/projects/{project_id}/export/estimate")
async def estimate_project_export(
    project_id: int,
    include_fastq: bool = False,
    current_user: dict = require_permission("files", "download"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    result = await export_service.estimate_project_export(session, project_id, org_id, include_fastq)
    if result is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return result


# ---------------------------------------------------------------------------
# Export endpoints
# ---------------------------------------------------------------------------


@router.post("/api/experiments/{experiment_id}/export/data")
async def export_experiment(
    experiment_id: int,
    body: ExportRequest,
    current_user: dict = require_permission("files", "download"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    user_email = current_user.get("email", "")

    try:
        zip_bytes = await export_service.export_experiment(
            session=session,
            experiment_id=experiment_id,
            org_id=org_id,
            include_fastq=body.include_fastq,
            include_provenance=body.include_provenance,
            user_email=user_email,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    await log_action(
        session=session,
        user_id=user_id,
        entity_type="experiment",
        entity_id=experiment_id,
        action="data_exported",
        details={
            "delivery_method": body.delivery_method,
            "include_fastq": body.include_fastq,
            "include_provenance": body.include_provenance,
        },
    )

    if body.delivery_method == "gcs":
        try:
            signed_url = await export_service._upload_zip_to_gcs(
                zip_bytes, org_id, f"experiment_{experiment_id}", session
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"GCS upload failed: {exc}")
        await session.commit()
        return {"signed_url": signed_url, "expires_in_hours": 24}

    await session.commit()

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"experiment_{experiment_id}_export_{ts}.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/projects/{project_id}/export/data")
async def export_project(
    project_id: int,
    body: ExportRequest,
    current_user: dict = require_permission("files", "download"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    user_email = current_user.get("email", "")

    try:
        zip_bytes, filename = await export_service.export_project(
            session=session,
            project_id=project_id,
            org_id=org_id,
            include_fastq=body.include_fastq,
            include_provenance=body.include_provenance,
            user_email=user_email,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    await log_action(
        session=session,
        user_id=user_id,
        entity_type="project",
        entity_id=project_id,
        action="data_exported",
        details={
            "delivery_method": body.delivery_method,
            "include_fastq": body.include_fastq,
            "include_provenance": body.include_provenance,
        },
    )

    if body.delivery_method == "gcs":
        try:
            signed_url = await export_service._upload_zip_to_gcs(zip_bytes, org_id, f"project_{project_id}", session)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"GCS upload failed: {exc}")
        await session.commit()
        return {"signed_url": signed_url, "expires_in_hours": 24}

    await session.commit()

    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
