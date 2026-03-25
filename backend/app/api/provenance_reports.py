"""API endpoints for provenance report generation (JSON, CSV, PDF)."""

from __future__ import annotations

from enum import Enum

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.services.audit_service import log_action
from app.services.provenance.report_service import ProvenanceReportService

router = APIRouter()


class ReportFormat(str, Enum):
    json = "json"
    csv = "csv"
    pdf = "pdf"


async def _generate_report(
    entity_type: str,
    entity_id: int,
    format: ReportFormat,
    current_user: dict,
    session: AsyncSession,
) -> Response:
    org_id = int(current_user["org_id"])
    user_email = current_user.get("email", "")
    result = await ProvenanceReportService.generate(
        session=session,
        entity_type=entity_type,
        entity_id=entity_id,
        org_id=org_id,
        user_email=user_email,
        format=format.value,
    )

    await log_action(
        session=session,
        user_id=int(current_user["sub"]),
        entity_type=entity_type,
        entity_id=entity_id,
        action="provenance_export",
        details={"format": format.value},
    )

    content = result.content
    if isinstance(content, str):
        content = content.encode("utf-8")

    return Response(
        content=content,
        media_type=result.content_type,
        headers={"Content-Disposition": f'attachment; filename="{result.filename}"'},
    )


@router.get("/api/experiments/{experiment_id}/provenance/report")
async def experiment_provenance_report(
    experiment_id: int,
    format: ReportFormat = Query(ReportFormat.json),
    current_user: dict = require_permission("experiments", "view"),
    session: AsyncSession = Depends(get_session),
) -> Response:
    return await _generate_report("experiment", experiment_id, format, current_user, session)


@router.get("/api/projects/{project_id}/provenance/report")
async def project_provenance_report(
    project_id: int,
    format: ReportFormat = Query(ReportFormat.json),
    current_user: dict = require_permission("projects", "view"),
    session: AsyncSession = Depends(get_session),
) -> Response:
    return await _generate_report("project", project_id, format, current_user, session)


@router.get("/api/samples/{sample_id}/provenance/report")
async def sample_provenance_report(
    sample_id: int,
    format: ReportFormat = Query(ReportFormat.json),
    current_user: dict = require_permission("samples", "view"),
    session: AsyncSession = Depends(get_session),
) -> Response:
    return await _generate_report("sample", sample_id, format, current_user, session)


@router.get("/api/pipeline-runs/{run_id}/provenance/report")
async def pipeline_run_provenance_report(
    run_id: int,
    format: ReportFormat = Query(ReportFormat.json),
    current_user: dict = require_permission("pipelines", "view"),
    session: AsyncSession = Depends(get_session),
) -> Response:
    return await _generate_report("pipeline_run", run_id, format, current_user, session)


@router.get("/api/files/{file_id}/provenance/report")
async def artifact_provenance_report(
    file_id: int,
    format: ReportFormat = Query(ReportFormat.json),
    current_user: dict = require_permission("files", "view"),
    session: AsyncSession = Depends(get_session),
) -> Response:
    return await _generate_report("artifact", file_id, format, current_user, session)
