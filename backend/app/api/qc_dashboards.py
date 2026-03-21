from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_role
from app.database import get_session
from app.models.file import File
from app.schemas.qc_dashboard import (
    QCDashboardResponse,
    QCDashboardSummary,
    QCMetrics,
    QCPlot,
)
from app.services.qc_dashboard_service import QCDashboardService

router = APIRouter(prefix="/api/qc-dashboards", tags=["qc-dashboards"])


def _dashboard_response(d) -> QCDashboardResponse:
    metrics = d.metrics_json or {}
    plots = d.plots_json if isinstance(d.plots_json, list) else []

    return QCDashboardResponse(
        id=d.id,
        pipeline_run_id=d.pipeline_run_id,
        experiment_id=d.experiment_id,
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


def _dashboard_summary(d) -> QCDashboardSummary:
    metrics = d.metrics_json or {}
    return QCDashboardSummary(
        id=d.id,
        pipeline_run_id=d.pipeline_run_id,
        quality_rating=metrics.get("quality_rating", "concerning"),
        cell_count=metrics.get("cell_count"),
        status=d.status,
        generated_at=d.generated_at,
    )


@router.get("")
async def list_dashboards(
    request: Request,
    experiment_id: int | None = None,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    dashboards = await QCDashboardService.list_dashboards(session, org_id, experiment_id)
    return [_dashboard_summary(d) for d in dashboards]


@router.get("/{dashboard_id}", response_model=QCDashboardResponse)
async def get_dashboard(
    dashboard_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    d = await QCDashboardService.get_dashboard(session, org_id, dashboard_id)
    if not d:
        raise HTTPException(404, "QC Dashboard not found")
    return _dashboard_response(d)


@router.get("/by-run/{pipeline_run_id}", response_model=QCDashboardResponse)
async def get_dashboard_by_run(
    pipeline_run_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    d = await QCDashboardService.get_dashboard_by_run(session, org_id, pipeline_run_id)
    if not d:
        raise HTTPException(404, "QC Dashboard not found for this pipeline run")
    return _dashboard_response(d)


@router.post("/generate/{pipeline_run_id}", response_model=QCDashboardResponse)
async def generate_dashboard(
    pipeline_run_id: int,
    current_user: dict = require_role("admin", "comp_bio"),
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
        return _dashboard_response(d)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/regenerate/{pipeline_run_id}", response_model=QCDashboardResponse)
async def regenerate_dashboard(
    pipeline_run_id: int,
    current_user: dict = require_role("admin", "comp_bio"),
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
        return _dashboard_response(d)
    except ValueError as e:
        raise HTTPException(400, str(e))
