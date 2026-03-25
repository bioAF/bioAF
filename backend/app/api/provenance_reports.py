"""API endpoints for provenance report generation (JSON, CSV, PDF, Markdown, all)."""

from __future__ import annotations

from enum import Enum

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.models.experiment import Experiment
from app.models.file import File
from app.models.pipeline_run import PipelineRun
from app.models.project import Project
from app.models.sample import Sample
from app.services.audit_service import log_action
from app.services.provenance.report_service import ProvenanceReportService

router = APIRouter()


class ReportFormat(str, Enum):
    json = "json"
    md = "md"
    csv = "csv"
    pdf = "pdf"
    all = "all"


async def _check_entity_exists(session: AsyncSession, entity_type: str, entity_id: int, org_id: int) -> None:
    """Raise 404 if entity doesn't exist or belongs to another org."""
    if entity_type == "sample":
        # Samples are org-scoped through their experiment
        stmt = (
            select(Sample)
            .join(Experiment, Sample.experiment_id == Experiment.id)
            .where(Sample.id == entity_id, Experiment.organization_id == org_id)
        )
    else:
        model_map: dict[str, tuple] = {
            "experiment": (Experiment, Experiment.id, Experiment.organization_id),
            "project": (Project, Project.id, Project.organization_id),
            "pipeline_run": (PipelineRun, PipelineRun.id, PipelineRun.organization_id),
            "artifact": (File, File.id, File.organization_id),
        }
        entry = model_map.get(entity_type)
        if not entry:
            raise HTTPException(status_code=404)
        model, id_col, org_col = entry
        stmt = select(model).where(id_col == entity_id, org_col == org_id)

    result = await session.execute(stmt)
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail=f"{entity_type} not found")


async def _generate_report(
    entity_type: str,
    entity_id: int,
    format: ReportFormat,
    current_user: dict,
    session: AsyncSession,
) -> Response:
    org_id = int(current_user["org_id"])
    await _check_entity_exists(session, entity_type, entity_id, org_id)
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
        action="provenance_report_generated",
        details={"format": format.value, "entity_type": entity_type},
    )
    await session.commit()

    content = result.content
    if isinstance(content, str):
        content = content.encode("utf-8")

    headers: dict[str, str] = {}
    if format != ReportFormat.json:
        headers["Content-Disposition"] = f'attachment; filename="{result.filename}"'

    return Response(
        content=content,
        media_type=result.content_type,
        headers=headers,
    )


@router.get("/api/experiments/{experiment_id}/provenance/report")
async def experiment_provenance_report(
    experiment_id: int,
    format: ReportFormat = Query(ReportFormat.json),
    current_user: dict = require_permission("files", "download"),
    session: AsyncSession = Depends(get_session),
) -> Response:
    return await _generate_report("experiment", experiment_id, format, current_user, session)


@router.get("/api/projects/{project_id}/provenance/report")
async def project_provenance_report(
    project_id: int,
    format: ReportFormat = Query(ReportFormat.json),
    current_user: dict = require_permission("files", "download"),
    session: AsyncSession = Depends(get_session),
) -> Response:
    return await _generate_report("project", project_id, format, current_user, session)


@router.get("/api/samples/{sample_id}/provenance/report")
async def sample_provenance_report(
    sample_id: int,
    format: ReportFormat = Query(ReportFormat.json),
    current_user: dict = require_permission("files", "download"),
    session: AsyncSession = Depends(get_session),
) -> Response:
    return await _generate_report("sample", sample_id, format, current_user, session)


@router.get("/api/pipeline-runs/{run_id}/provenance/report")
async def pipeline_run_provenance_report(
    run_id: int,
    format: ReportFormat = Query(ReportFormat.json),
    current_user: dict = require_permission("files", "download"),
    session: AsyncSession = Depends(get_session),
) -> Response:
    return await _generate_report("pipeline_run", run_id, format, current_user, session)


@router.get("/api/files/{file_id}/provenance/report")
async def artifact_provenance_report(
    file_id: int,
    format: ReportFormat = Query(ReportFormat.json),
    current_user: dict = require_permission("files", "download"),
    session: AsyncSession = Depends(get_session),
) -> Response:
    return await _generate_report("artifact", file_id, format, current_user, session)
