"""Data export API endpoints for experiments and projects."""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.registry import require_capability
from app.api.dependencies import require_permission
from app.api.streaming_responses import GuardedFileResponse
from app.config import settings
from app.database import get_session
from app.services import export_service, export_delivery
from app.services.audit_service import log_action
from app.services.data_export import resources

logger = logging.getLogger("bioaf.data_export.api")

router = APIRouter(tags=["data-export"])


class ExportRequest(BaseModel):
    # "gcs" is the signed-link delivery on any store that can sign links (the name predates S3).
    delivery_method: Literal["direct", "gcs"]
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
    if body.delivery_method == "gcs":
        require_capability("signed_url_upload")
        await export_delivery.check_signing_available()
    prepared = await export_service.prepare_experiment_export(
        session,
        experiment_id,
        int(current_user["org_id"]),
        include_fastq=body.include_fastq,
        include_provenance=body.include_provenance,
        user_email=current_user.get("email", ""),
    )
    return await _deliver(session, current_user, "experiment", experiment_id, body, prepared)


@router.post("/api/projects/{project_id}/export/data")
async def export_project(
    project_id: int,
    body: ExportRequest,
    current_user: dict = require_permission("files", "download"),
    session: AsyncSession = Depends(get_session),
):
    if body.delivery_method == "gcs":
        require_capability("signed_url_upload")
        await export_delivery.check_signing_available()
    prepared = await export_service.prepare_project_export(
        session,
        project_id,
        int(current_user["org_id"]),
        include_fastq=body.include_fastq,
        include_provenance=body.include_provenance,
        user_email=current_user.get("email", ""),
    )
    return await _deliver(session, current_user, "project", project_id, body, prepared)


async def _audit(session, current_user: dict, entity_type: str, entity_id: int, body: ExportRequest, **stage) -> None:
    """The ``data_exported`` event: what the server prepared or issued, never what the client received."""
    await log_action(
        session=session,
        user_id=int(current_user["sub"]),
        entity_type=entity_type,
        entity_id=entity_id,
        action="data_exported",
        details={
            "delivery_method": body.delivery_method,
            "include_fastq": body.include_fastq,
            "include_provenance": body.include_provenance,
            **stage,
        },
    )


async def _deliver(session, current_user, entity_type, entity_id, body, prepared: export_service.PreparedExport):
    try:
        if body.delivery_method == "gcs":
            link = await export_delivery.issue_signed_link(prepared, org_id=int(current_user["org_id"]))
            try:
                await _audit(
                    session,
                    current_user,
                    entity_type,
                    entity_id,
                    body,
                    stage="link_issued",
                    archive_bytes=prepared.size,
                    link_expires_at=link.expires_at.isoformat(),
                )
                await session.commit()
            except BaseException:
                await export_delivery.withdraw(link)
                raise
            prepared.release()
            return {"signed_url": link.url, "expires_in_hours": link.expires_in_hours}

        async def finished(outcome: str | None) -> None:
            prepared.release()
            if outcome != "sent":
                logger.warning("Data export transfer ended: %s", outcome)

        response = GuardedFileResponse(
            prepared.path,
            size=prepared.size,
            filename=prepared.filename,
            media_type="application/zip",
            on_close=finished,
            on_start=lambda: setattr(prepared, "claimed", True),
            stall_seconds=settings.export_transfer_stall_seconds,
            deadline_seconds=resources.TRANSFER_DEADLINE,
        )
        await _audit(
            session, current_user, entity_type, entity_id, body, stage="archive_prepared", archive_bytes=prepared.size
        )
        await session.commit()
    except BaseException:
        prepared.release()
        raise
    export_delivery.watch_unclaimed(prepared)
    return response
