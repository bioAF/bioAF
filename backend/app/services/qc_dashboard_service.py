"""Thin orchestrator for QC dashboard generation.

Per-template extractor + render config + quality rating + summary all live
under app/services/qc/templates/. The service resolves the right template,
calls into it, persists the dashboard, and emits an audit log entry.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.file import File
from app.models.pipeline_run import PipelineRun
from app.models.qc_dashboard import QCDashboard
from app.services.audit_service import log_action
from app.services.file_service import FileService
from app.services.gcs_storage import GcsStorageService
from app.services.qc.extractors.gcs_helpers import get_results_bucket
from app.services.qc.resolver import resolve_template_for_run
from app.services.qc.templates import get_template, scrnaseq as scrnaseq_template

logger = logging.getLogger("bioaf.qc_dashboard_service")


class QCDashboardService:
    @staticmethod
    async def generate_qc_dashboard(
        session: AsyncSession, org_id: int, pipeline_run_id: int, *, skip_cache: bool = False
    ) -> QCDashboard:
        """Generate a QC dashboard from pipeline run output files."""
        result = await session.execute(
            select(PipelineRun).where(
                PipelineRun.id == pipeline_run_id,
                PipelineRun.organization_id == org_id,
            )
        )
        run = result.scalar_one_or_none()
        if not run:
            raise ValueError(f"Pipeline run {pipeline_run_id} not found")

        dashboard = QCDashboard(
            organization_id=org_id,
            pipeline_run_id=pipeline_run_id,
            experiment_id=run.experiment_id,
            metrics_json={},
            status="generating",
        )
        session.add(dashboard)
        await session.flush()

        try:
            template_name, render_config = await resolve_template_for_run(session, run)
            template = get_template(template_name)

            metrics = await QCDashboardService._extract_metrics(
                session, run, template_name=template_name, skip_cache=skip_cache
            )

            quality = template.compute_quality(metrics)
            metrics["quality_rating"] = quality

            summary = QCDashboardService._generate_summary(template_name, metrics)

            plots_meta = await QCDashboardService._collect_plots(session, org_id, run, template_name=template_name)

            dashboard.metrics_json = metrics
            dashboard.summary_text = summary
            dashboard.plots_json = plots_meta
            dashboard.qc_config_json = render_config
            dashboard.status = "ready"
            dashboard.generated_at = datetime.now(timezone.utc)

        except Exception as e:
            logger.error("QC dashboard generation failed for run %d: %s", pipeline_run_id, e)
            dashboard.status = "failed"
            dashboard.summary_text = f"Generation failed: {e}"

        await log_action(
            session,
            user_id=run.submitted_by_user_id,
            entity_type="qc_dashboard",
            entity_id=dashboard.id,
            action="generate",
            details={"pipeline_run_id": pipeline_run_id, "status": dashboard.status},
        )
        await session.flush()
        return dashboard

    @staticmethod
    async def get_dashboard(session: AsyncSession, org_id: int, dashboard_id: int) -> QCDashboard | None:
        result = await session.execute(
            select(QCDashboard).where(
                QCDashboard.id == dashboard_id,
                QCDashboard.organization_id == org_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_dashboard_by_run(session: AsyncSession, org_id: int, pipeline_run_id: int) -> QCDashboard | None:
        result = await session.execute(
            select(QCDashboard).where(
                QCDashboard.pipeline_run_id == pipeline_run_id,
                QCDashboard.organization_id == org_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_dashboards(
        session: AsyncSession, org_id: int, experiment_id: int | None = None
    ) -> list[QCDashboard]:
        query = select(QCDashboard).where(QCDashboard.organization_id == org_id)
        if experiment_id:
            query = query.where(QCDashboard.experiment_id == experiment_id)
        query = query.order_by(QCDashboard.created_at.desc())
        result = await session.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def _extract_metrics(
        session: AsyncSession,
        run: PipelineRun,
        *,
        template_name: str = "scrnaseq",
        skip_cache: bool = False,
    ) -> dict:
        """Dispatch metric extraction to the template module.

        scRNA-seq + bulk_rnaseq currently share the same extractor (the existing
        STARsolo/h5ad/MultiQC pipeline). Custom pipelines drop their own
        qc_metrics.json, which the dispatcher reads as-is.
        """
        results_bucket = await QCDashboardService._get_results_bucket(session)
        if not results_bucket:
            logger.warning("No results bucket configured, cannot extract metrics")
            if template_name == "custom":
                return {}
            return dict(scrnaseq_template.EMPTY_METRICS)

        if template_name == "custom":
            return await QCDashboardService._extract_custom_metrics(
                session, run, skip_cache=skip_cache, results_bucket=results_bucket
            )
        return await scrnaseq_template.extract(session, run, skip_cache=skip_cache, results_bucket=results_bucket)

    @staticmethod
    async def _extract_custom_metrics(
        session: AsyncSession,
        run: PipelineRun,
        *,
        skip_cache: bool = False,
        results_bucket: str | None = None,
    ) -> dict:
        """Custom pipelines emit qc_metrics.json directly into the run output prefix."""
        if results_bucket is None:
            results_bucket = await QCDashboardService._get_results_bucket(session)
        if not results_bucket:
            logger.warning("No results bucket configured, cannot extract metrics")
            return {}

        credentials = await GcsStorageService.get_credentials(session)

        try:
            import json as _json

            from google.cloud import storage

            client = storage.Client(credentials=credentials)
            bucket = client.bucket(results_bucket)
            prefix = f"experiments/{run.experiment_id}/pipeline-runs/{run.id}/"

            cache_blob = bucket.blob(f"{prefix}qc_metrics.json")
            if cache_blob.exists():
                return _json.loads(cache_blob.download_as_text())

            logger.info("No qc_metrics.json found for custom pipeline run %d", run.id)
            return {}
        except Exception as e:
            logger.warning("Custom-pipeline metric extraction failed for run %d: %s", run.id, e)
            return {}

    @staticmethod
    def _generate_summary(template_name: str, metrics: dict) -> str:
        """Currently both built-in templates use the scrnaseq summary generator
        (it covers FastQC + STAR fields used by bulk RNA-seq too). Custom
        pipelines fall back to a one-liner if they didn't emit one."""
        if template_name == "custom":
            emitted = metrics.get("summary_text")
            if isinstance(emitted, str) and emitted:
                return emitted
            quality = metrics.get("quality_rating", "pending_review")
            return f"Custom pipeline run. Overall quality: **{quality.capitalize()}**."
        return scrnaseq_template.generate_summary(metrics)

    @staticmethod
    async def _collect_plots(
        session: AsyncSession,
        org_id: int,
        run: PipelineRun,
        *,
        template_name: str = "scrnaseq",
    ) -> list[dict]:
        """Collect MultiQC plot PNGs the template advertises in its render config.

        scRNA-seq + bulk_rnaseq both ship the standard nf-core MultiQC PNG set;
        custom pipelines don't currently auto-collect plots (they can drop
        their own image files into qc_metrics.json if needed).
        """
        plots_meta: list[dict] = []
        if template_name == "custom":
            return plots_meta

        results_bucket = await get_results_bucket(session)
        if not results_bucket:
            return plots_meta

        try:
            from google.cloud import storage

            credentials = await GcsStorageService.get_credentials(session)
            client = storage.Client(credentials=credentials)
            bucket = client.bucket(results_bucket)
            prefix = f"experiments/{run.experiment_id}/pipeline-runs/{run.id}/"

            plot_prefix = f"{prefix}multiqc/multiqc_plots/png/"
            available: dict[str, "storage.Blob"] = {}
            for blob in bucket.list_blobs(prefix=plot_prefix):
                if blob.name.endswith(".png"):
                    filename = blob.name.rsplit("/", 1)[-1]
                    available[filename] = blob

            for png_name, title, plot_type in scrnaseq_template.MULTIQC_PLOTS:
                blob_obj = available.get(png_name)
                if not blob_obj:
                    continue

                gcs_uri = f"gs://{results_bucket}/{blob_obj.name}"

                file = await FileService.create_file_record(
                    session,
                    org_id=org_id,
                    user_id=None,
                    filename=png_name,
                    gcs_uri=gcs_uri,
                    size_bytes=blob_obj.size,
                    md5_checksum=None,
                    file_type="png",
                    tags=["qc_plot", plot_type],
                    experiment_id=run.experiment_id,
                    source_type="qc_dashboard",
                    source_pipeline_run_id=run.id,
                )
                plots_meta.append({"plot_type": plot_type, "title": title, "file_id": file.id})

            logger.info("Collected %d plots from GCS for run %d", len(plots_meta), run.id)

        except Exception as e:
            logger.warning("Plot collection from GCS failed for run %d: %s", run.id, e)

        return plots_meta

    # Backwards-compat shims -- existing tests + callers reference these as
    # static methods on the service. Keep them as thin wrappers that delegate
    # to the scrnaseq template module (the canonical home for these helpers).

    @staticmethod
    def _read_starsolo_summary(summary_csv_text: str) -> dict:
        return scrnaseq_template.read_starsolo_summary(summary_csv_text)

    @staticmethod
    def _read_h5ad_metrics(path: str) -> dict:
        return scrnaseq_template.read_h5ad_metrics(path)

    @staticmethod
    def _read_multiqc_metrics(multiqc_json_text: str) -> dict:
        return scrnaseq_template.read_multiqc_metrics(multiqc_json_text)

    @staticmethod
    def _read_multiqc_chart_data(multiqc_json_text: str) -> dict:
        return scrnaseq_template.read_multiqc_chart_data(multiqc_json_text)

    @staticmethod
    def _read_umi_per_cell_sorted(text_data: str) -> list[list[int]]:
        return scrnaseq_template.read_umi_per_cell_sorted(text_data)

    @staticmethod
    def _build_barcode_rank_data(umi_counts: list[int], max_points: int = 500) -> list[list[int]]:
        return scrnaseq_template.build_barcode_rank_data(umi_counts, max_points=max_points)

    @staticmethod
    def _extract_barcode_rank_from_mtx(mtx_text: str) -> list[list[int]]:
        return scrnaseq_template.extract_barcode_rank_from_mtx(mtx_text)

    @staticmethod
    def _compute_quality_rating(metrics: dict) -> str:
        return scrnaseq_template.compute_quality(metrics)

    @staticmethod
    def _generate_summary_legacy(metrics: dict) -> str:
        return scrnaseq_template.generate_summary(metrics)

    @staticmethod
    async def _get_results_bucket(session: AsyncSession) -> str | None:
        return await get_results_bucket(session)


_ = File  # keep import live for downstream services that import through here
