from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission, require_results_view
from app.database import get_session
from app.models.experiment import Experiment
from app.models.file import File
from app.models.pipeline_run import PipelineRun, PipelineRunSample
from app.models.project import Project
from app.models.sample import Sample
from app.schemas.qc_dashboard import (
    QCDashboardConfig,
    QCDashboardResponse,
    QCDashboardSummary,
    QCMetrics,
    QCPlot,
)
from app.services.qc.resolver import build_config_for_template, resolve_template_for_run
from app.services.qc_dashboard_service import QCDashboardService

router = APIRouter(prefix="/api/qc-dashboards", tags=["qc-dashboards"])


def _dashboard_response(d, qc_config: dict, context: dict | None = None) -> QCDashboardResponse:
    metrics = d.metrics_json or {}
    plots = d.plots_json if isinstance(d.plots_json, list) else []
    ctx = context or {}

    return QCDashboardResponse(
        id=d.id,
        pipeline_run_id=d.pipeline_run_id,
        experiment_id=d.experiment_id,
        pipeline_name=ctx.get("pipeline_name"),
        pipeline_version=ctx.get("pipeline_version"),
        project_name=ctx.get("project_name"),
        experiment_name=ctx.get("experiment_name"),
        qc_config=QCDashboardConfig(**qc_config),
        raw_metrics=metrics,
        metrics=QCMetrics(
            cell_count=metrics.get("cell_count"),
            median_reads_per_cell=metrics.get("median_reads_per_cell"),
            median_genes_per_cell=metrics.get("median_genes_per_cell"),
            median_umi_per_cell=metrics.get("median_umi_per_cell"),
            mito_pct_median=metrics.get("mito_pct_median"),
            doublet_score_median=metrics.get("doublet_score_median"),
            saturation=metrics.get("saturation"),
            total_sequences=metrics.get("total_sequences"),
            percent_duplicates=metrics.get("percent_duplicates"),
            percent_gc=metrics.get("percent_gc"),
            avg_sequence_length=metrics.get("avg_sequence_length"),
            total_samples=metrics.get("total_samples"),
            quality_rating=metrics.get("quality_rating", "concerning"),
            number_of_reads=metrics.get("number_of_reads"),
            valid_barcodes=metrics.get("valid_barcodes"),
            q30_bases_barcode=metrics.get("q30_bases_barcode"),
            q30_bases_rna_read=metrics.get("q30_bases_rna_read"),
            reads_mapped_genome=metrics.get("reads_mapped_genome"),
            reads_mapped_genome_unique=metrics.get("reads_mapped_genome_unique"),
            mean_reads_per_cell=metrics.get("mean_reads_per_cell"),
            mean_umi_per_cell=metrics.get("mean_umi_per_cell"),
            mean_genes_per_cell=metrics.get("mean_genes_per_cell"),
            total_genes_detected=metrics.get("total_genes_detected"),
            umis_in_cells=metrics.get("umis_in_cells"),
            barcode_rank_data=metrics.get("barcode_rank_data"),
            chart_data=metrics.get("chart_data"),
        ),
        summary_text=d.summary_text or "",
        plots=[
            QCPlot(
                plot_type=p.get("plot_type", ""),
                title=p.get("title", ""),
                file_id=p.get("file_id", 0),
            )
            for p in plots
        ],
        status=d.status,
        generated_at=d.generated_at,
        created_at=d.created_at,
    )


async def _resolve_dashboard_config(session, dashboard) -> dict:
    """Return the render config for a dashboard.

    Pre-snapshot rows have qc_config_json = NULL; in that case re-resolve
    from the run's pipeline so the page still renders.
    """
    if dashboard.qc_config_json:
        return dashboard.qc_config_json

    from app.models.pipeline_run import PipelineRun

    run = await session.get(PipelineRun, dashboard.pipeline_run_id)
    if run is None:
        return build_config_for_template("scrnaseq")
    _, cfg = await resolve_template_for_run(session, run)
    return cfg


def _dashboard_summary(
    d,
    *,
    project_name: str | None = None,
    experiment_name: str | None = None,
    pipeline_name: str | None = None,
    pipeline_version: str | None = None,
    sample_external_ids: list[str] | None = None,
) -> QCDashboardSummary:
    metrics = d.metrics_json or {}
    return QCDashboardSummary(
        id=d.id,
        pipeline_run_id=d.pipeline_run_id,
        quality_rating=metrics.get("quality_rating", "concerning"),
        cell_count=metrics.get("cell_count"),
        status=d.status,
        generated_at=d.generated_at,
        project_name=project_name,
        experiment_name=experiment_name,
        pipeline_name=pipeline_name,
        pipeline_version=pipeline_version,
        sample_external_ids=sample_external_ids or [],
    )


async def _load_summary_context(session: AsyncSession, org_id: int, dashboards) -> dict[int, dict]:
    """Batch-load project, experiment, pipeline, and sample external_ids for
    each dashboard's pipeline_run. Returns a map: dashboard_id -> context."""
    if not dashboards:
        return {}

    run_ids = [d.pipeline_run_id for d in dashboards]

    run_rows = (
        await session.execute(
            select(
                PipelineRun.id,
                PipelineRun.pipeline_name,
                PipelineRun.pipeline_version,
                Project.name,
                Experiment.name,
            )
            .outerjoin(Project, Project.id == PipelineRun.project_id)
            .outerjoin(Experiment, Experiment.id == PipelineRun.experiment_id)
            .where(
                PipelineRun.id.in_(run_ids),
                PipelineRun.organization_id == org_id,
            )
        )
    ).all()
    run_ctx: dict[int, dict] = {
        row[0]: {
            "pipeline_name": row[1],
            "pipeline_version": row[2],
            "project_name": row[3],
            "experiment_name": row[4],
            "sample_external_ids": [],
        }
        for row in run_rows
    }

    sample_rows = (
        await session.execute(
            select(PipelineRunSample.pipeline_run_id, Sample.external_id)
            .join(Sample, Sample.id == PipelineRunSample.sample_id)
            .where(PipelineRunSample.pipeline_run_id.in_(run_ids))
        )
    ).all()
    for run_id, external_id in sample_rows:
        if run_id in run_ctx and external_id:
            run_ctx[run_id]["sample_external_ids"].append(external_id)

    return {d.id: run_ctx.get(d.pipeline_run_id, {}) for d in dashboards}


async def _dashboard_context(session: AsyncSession, org_id: int, d) -> dict:
    """Run context (project / experiment / pipeline names) for a single dashboard."""
    return (await _load_summary_context(session, org_id, [d])).get(d.id, {})


@router.get("")
async def list_dashboards(
    request: Request,
    experiment_id: int | None = None,
    _user: dict = require_results_view(),
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    dashboards = await QCDashboardService.list_dashboards(session, org_id, experiment_id)
    ctx_by_id = await _load_summary_context(session, org_id, dashboards)
    return [
        _dashboard_summary(
            d,
            project_name=ctx_by_id.get(d.id, {}).get("project_name"),
            experiment_name=ctx_by_id.get(d.id, {}).get("experiment_name"),
            pipeline_name=ctx_by_id.get(d.id, {}).get("pipeline_name"),
            pipeline_version=ctx_by_id.get(d.id, {}).get("pipeline_version"),
            sample_external_ids=ctx_by_id.get(d.id, {}).get("sample_external_ids", []),
        )
        for d in dashboards
    ]


@router.get("/{dashboard_id}", response_model=QCDashboardResponse)
async def get_dashboard(
    dashboard_id: int,
    request: Request,
    _user: dict = require_results_view(),
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    d = await QCDashboardService.get_dashboard(session, org_id, dashboard_id)
    if not d:
        raise HTTPException(404, "QC Dashboard not found")
    cfg = await _resolve_dashboard_config(session, d)
    ctx = await _dashboard_context(session, org_id, d)
    return _dashboard_response(d, cfg, ctx)


@router.get("/by-run/{pipeline_run_id}", response_model=QCDashboardResponse)
async def get_dashboard_by_run(
    pipeline_run_id: int,
    request: Request,
    _user: dict = require_results_view(),
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    d = await QCDashboardService.get_dashboard_by_run(session, org_id, pipeline_run_id)
    if not d:
        raise HTTPException(404, "QC Dashboard not found for this pipeline run")
    cfg = await _resolve_dashboard_config(session, d)
    ctx = await _dashboard_context(session, org_id, d)
    return _dashboard_response(d, cfg, ctx)


@router.post("/generate/{pipeline_run_id}", response_model=QCDashboardResponse)
async def generate_dashboard(
    pipeline_run_id: int,
    current_user: dict = require_permission("experiments", "create"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])

    # Check component enabled
    from app.services.component_service import ComponentService

    if not await ComponentService.is_enabled(session, "qc_dashboard"):
        raise HTTPException(400, "QC Dashboard component is not enabled")

    try:
        d = await QCDashboardService.generate_qc_dashboard(session, org_id, pipeline_run_id)
        await session.commit()
        cfg = await _resolve_dashboard_config(session, d)
        ctx = await _dashboard_context(session, org_id, d)
        return _dashboard_response(d, cfg, ctx)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/regenerate/{pipeline_run_id}", response_model=QCDashboardResponse)
async def regenerate_dashboard(
    pipeline_run_id: int,
    current_user: dict = require_permission("experiments", "create"),
    session: AsyncSession = Depends(get_session),
):
    """Delete existing QC dashboard for a run and regenerate from current GCS data."""
    org_id = int(current_user["org_id"])

    from app.services.component_service import ComponentService

    if not await ComponentService.is_enabled(session, "qc_dashboard"):
        raise HTTPException(400, "QC Dashboard component is not enabled")

    # Delete existing dashboard and its plot file records
    existing = await QCDashboardService.get_dashboard_by_run(session, org_id, pipeline_run_id)
    if existing:
        # Clean up file records created by _collect_plots
        old_plots = existing.plots_json if isinstance(existing.plots_json, list) else []
        for plot in old_plots:
            file_id = plot.get("file_id")
            if file_id:
                await session.execute(sa_delete(File).where(File.id == file_id, File.organization_id == org_id))
        await session.delete(existing)
        await session.flush()

    try:
        d = await QCDashboardService.generate_qc_dashboard(session, org_id, pipeline_run_id, skip_cache=True)
        await session.commit()
        cfg = await _resolve_dashboard_config(session, d)
        ctx = await _dashboard_context(session, org_id, d)
        return _dashboard_response(d, cfg, ctx)
    except ValueError as e:
        raise HTTPException(400, str(e))
