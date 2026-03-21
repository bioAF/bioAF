import json
import logging
import tempfile
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline_run import PipelineRun
from app.models.qc_dashboard import QCDashboard
from app.services.audit_service import log_action
from app.services.file_service import FileService
from app.services.gcs_storage import GcsStorageService

logger = logging.getLogger("bioaf.qc_dashboard_service")


class QCDashboardService:
    @staticmethod
    async def generate_qc_dashboard(
        session: AsyncSession, org_id: int, pipeline_run_id: int, *, skip_cache: bool = False
    ) -> QCDashboard:
        """Generate a QC dashboard from pipeline run output files."""
        # Get pipeline run
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
            # Extract metrics from pipeline outputs (reads from GCS)
            metrics = await QCDashboardService._extract_metrics(session, run, skip_cache=skip_cache)

            # Compute quality rating
            quality = QCDashboardService._compute_quality_rating(metrics)
            metrics["quality_rating"] = quality

            # Generate summary text
            summary = QCDashboardService._generate_summary(metrics)

            # Collect real pipeline-generated plots from GCS
            plots_meta = await QCDashboardService._collect_plots(session, org_id, run)

            dashboard.metrics_json = metrics
            dashboard.summary_text = summary
            dashboard.plots_json = plots_meta
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

    # MultiQC plot PNGs we look for in GCS, mapped to display titles
    _MULTIQC_PLOTS: list[tuple[str, str, str]] = [
        ("star_alignment_plot-pct.png", "STAR Alignment", "star_alignment"),
        ("fastqc_per_base_sequence_quality_plot.png", "Per-Base Sequence Quality", "base_quality"),
        ("fastqc_per_sequence_gc_content_plot_Percentages.png", "GC Content Distribution", "gc_content"),
        ("fastqc_sequence_duplication_levels_plot.png", "Sequence Duplication Levels", "duplication"),
        ("fastqc_sequence_counts_plot-cnt.png", "Sequence Counts", "seq_counts"),
        ("general_stats_table.png", "General Statistics", "general_stats"),
    ]

    @staticmethod
    async def _extract_metrics(session: AsyncSession, run: PipelineRun, *, skip_cache: bool = False) -> dict:
        """Extract QC metrics from GCS.

        Checks for a cached qc_metrics.json first. If not found, downloads
        the h5ad output from GCS, extracts metrics locally, and writes the
        result back to GCS as a cache for future reads.
        """
        empty_metrics: dict = {
            "cell_count": None,
            "median_reads_per_cell": None,
            "median_genes_per_cell": None,
            "median_umi_per_cell": None,
            "mito_pct_median": None,
            "doublet_score_median": None,
            "saturation": None,
            "total_sequences": None,
            "percent_duplicates": None,
            "percent_gc": None,
            "avg_sequence_length": None,
            "total_samples": None,
            "number_of_reads": None,
            "valid_barcodes": None,
            "q30_bases_barcode": None,
            "q30_bases_rna_read": None,
            "reads_mapped_genome": None,
            "reads_mapped_genome_unique": None,
            "mean_reads_per_cell": None,
            "mean_umi_per_cell": None,
            "mean_genes_per_cell": None,
            "total_genes_detected": None,
            "umis_in_cells": None,
        }

        results_bucket = await QCDashboardService._get_results_bucket(session)
        if not results_bucket:
            logger.warning("No results bucket configured, cannot extract metrics")
            return empty_metrics

        logger.info(
            "Looking for metrics in gs://%s/experiments/%s/pipeline-runs/%s/", results_bucket, run.experiment_id, run.id
        )

        credentials = await GcsStorageService.get_credentials(session)

        try:
            from google.cloud import storage

            client = storage.Client(credentials=credentials)
            bucket = client.bucket(results_bucket)
            prefix = f"experiments/{run.experiment_id}/pipeline-runs/{run.id}/"

            # Log what files are at this prefix
            blobs_at_prefix = [b.name for b in bucket.list_blobs(prefix=prefix, max_results=20)]
            logger.info("Files at prefix for run %d: %s", run.id, blobs_at_prefix)

            # 1. Check for cached metrics JSON (skip on regenerate)
            cache_blob = bucket.blob(f"{prefix}qc_metrics.json")
            if not skip_cache and cache_blob.exists():
                cached = json.loads(cache_blob.download_as_text())
                logger.info("Using cached qc_metrics.json for run %d", run.id)
                return cached

            # Layer metrics from multiple sources. Each source fills in
            # what it can; later sources don't overwrite earlier values.
            metrics = dict(empty_metrics)

            # 2. Try STARsolo Summary.csv (best source for single-cell QC)
            starsolo_blob = None
            for blob in bucket.list_blobs(prefix=f"{prefix}star/"):
                if blob.name.endswith("Solo.out/Gene/Summary.csv"):
                    starsolo_blob = blob
                    break

            if starsolo_blob:
                logger.info("Found STARsolo Summary.csv for run %d", run.id)
                summary_text = starsolo_blob.download_as_text()
                starsolo_metrics = QCDashboardService._read_starsolo_summary(summary_text)
                for k, v in starsolo_metrics.items():
                    if v is not None:
                        metrics[k] = v

            # 3. Try h5ad for cell count (if STARsolo didn't provide one)
            if metrics["cell_count"] is None:
                h5ad_blob = None
                for blob in bucket.list_blobs(prefix=prefix):
                    if blob.name.endswith(".h5ad"):
                        h5ad_blob = blob
                        break

                if h5ad_blob:
                    with tempfile.NamedTemporaryFile(suffix=".h5ad") as tmp:
                        h5ad_blob.download_to_filename(tmp.name)
                        h5ad_metrics = QCDashboardService._read_h5ad_metrics(tmp.name)
                        for k, v in h5ad_metrics.items():
                            if v is not None and metrics.get(k) is None:
                                metrics[k] = v

            # 4. Try MultiQC data (FastQC aggregate stats, STAR alignment)
            multiqc_blob = None
            for blob in bucket.list_blobs(prefix=f"{prefix}multiqc/multiqc_data/"):
                if blob.name.endswith("multiqc_data.json"):
                    multiqc_blob = blob
                    break

            if multiqc_blob:
                logger.info("Found multiqc_data.json for run %d", run.id)
                multiqc_text = multiqc_blob.download_as_text()
                multiqc_metrics = QCDashboardService._read_multiqc_metrics(multiqc_text)
                for k, v in multiqc_metrics.items():
                    if v is not None and metrics.get(k) is None:
                        metrics[k] = v

                # Extract structured chart data for interactive rendering
                chart_data = QCDashboardService._read_multiqc_chart_data(multiqc_text)
                if chart_data:
                    metrics["chart_data"] = chart_data

            # 5. Try STARsolo UMIperCellSorted.txt for barcode rank (knee) plot
            umi_sorted_blob = None
            for blob in bucket.list_blobs(prefix=f"{prefix}star/"):
                if blob.name.endswith("Solo.out/Gene/UMIperCellSorted.txt"):
                    umi_sorted_blob = blob
                    break

            if umi_sorted_blob:
                logger.info("Found UMIperCellSorted.txt for barcode rank plot, run %d", run.id)
                try:
                    barcode_rank = QCDashboardService._read_umi_per_cell_sorted(umi_sorted_blob.download_as_text())
                    if barcode_rank:
                        metrics["barcode_rank_data"] = barcode_rank
                except Exception as e:
                    logger.warning("Barcode rank extraction failed for run %d: %s", run.id, e)
            else:
                # Fallback: try raw matrix.mtx
                raw_matrix_blob = None
                for blob in bucket.list_blobs(prefix=f"{prefix}star/"):
                    if blob.name.endswith("Solo.out/Gene/raw/UniqueAndMult-EM.mtx") or blob.name.endswith(
                        "Solo.out/Gene/raw/matrix.mtx"
                    ):
                        raw_matrix_blob = blob
                        break
                if raw_matrix_blob:
                    logger.info("Found raw matrix for barcode rank plot, run %d", run.id)
                    try:
                        barcode_rank = QCDashboardService._extract_barcode_rank_from_mtx(
                            raw_matrix_blob.download_as_text()
                        )
                        if barcode_rank:
                            metrics["barcode_rank_data"] = barcode_rank
                    except Exception as e:
                        logger.warning("Barcode rank extraction failed for run %d: %s", run.id, e)

            has_any = any(v is not None for k, v in metrics.items() if k not in ("chart_data", "barcode_rank_data"))
            if not has_any:
                logger.info("No metrics found for run %d from any source", run.id)
                return empty_metrics

            # 6. Upload metrics cache to GCS
            cache_upload_blob = bucket.blob(f"{prefix}qc_metrics.json")
            cache_upload_blob.upload_from_string(
                json.dumps(metrics, indent=2),
                content_type="application/json",
            )
            logger.info("Wrote qc_metrics.json cache for run %d", run.id)

            return metrics

        except Exception as e:
            logger.warning("Metric extraction from GCS failed for run %d: %s", run.id, e)
            return empty_metrics

    @staticmethod
    def _read_h5ad_metrics(path: str) -> dict:
        """Read an h5ad file and extract QC metrics."""
        metrics: dict = {
            "cell_count": None,
            "median_reads_per_cell": None,
            "median_genes_per_cell": None,
            "median_umi_per_cell": None,
            "mito_pct_median": None,
            "doublet_score_median": None,
            "saturation": None,
        }

        try:
            import anndata
            import numpy as np

            adata = anndata.read_h5ad(path)
            metrics["cell_count"] = int(adata.n_obs)

            obs_cols = list(adata.obs.columns)
            logger.info("h5ad obs columns (%d cells, %d genes): %s", adata.n_obs, adata.n_vars, obs_cols)

            # Genes per cell -- try common column name variants
            for col in ("n_genes", "n_genes_by_counts", "genes_detected", "nFeature_RNA", "n_features"):
                if col in obs_cols:
                    metrics["median_genes_per_cell"] = float(np.median(adata.obs[col]))
                    break

            # UMI / total counts per cell
            for col in ("total_counts", "nCount_RNA", "n_counts", "total_umi"):
                if col in obs_cols:
                    metrics["median_umi_per_cell"] = float(np.median(adata.obs[col]))
                    break

            # Mitochondrial percentage
            for col in ("pct_counts_mt", "pct_counts_mito", "percent.mt", "percent_mito", "mito_pct"):
                if col in obs_cols:
                    metrics["mito_pct_median"] = float(np.median(adata.obs[col]))
                    break

            # Doublet score
            for col in ("doublet_score", "scrublet_score", "doublet_scores"):
                if col in obs_cols:
                    metrics["doublet_score_median"] = float(np.median(adata.obs[col]))
                    break

        except ImportError:
            logger.warning("anndata not installed, cannot extract h5ad metrics")
        except Exception as e:
            logger.warning("h5ad metric extraction failed: %s", e)

        return metrics

    @staticmethod
    def _read_starsolo_summary(summary_csv_text: str) -> dict:
        """Parse STARsolo Gene/Summary.csv for single-cell QC metrics."""
        metrics: dict = {
            "cell_count": None,
            "median_reads_per_cell": None,
            "median_genes_per_cell": None,
            "median_umi_per_cell": None,
            "saturation": None,
            "number_of_reads": None,
            "valid_barcodes": None,
            "q30_bases_barcode": None,
            "q30_bases_rna_read": None,
            "reads_mapped_genome": None,
            "reads_mapped_genome_unique": None,
            "mean_reads_per_cell": None,
            "mean_umi_per_cell": None,
            "mean_genes_per_cell": None,
            "total_genes_detected": None,
            "umis_in_cells": None,
        }
        try:
            kv = {}
            for line in summary_csv_text.strip().splitlines():
                if "," in line:
                    key, val = line.split(",", 1)
                    kv[key.strip()] = val.strip()

            # Original fields
            if "Estimated Number of Cells" in kv:
                metrics["cell_count"] = int(kv["Estimated Number of Cells"])
            if "Median Reads per Cell" in kv:
                metrics["median_reads_per_cell"] = float(kv["Median Reads per Cell"])
            if "Median Gene per Cell" in kv:
                metrics["median_genes_per_cell"] = float(kv["Median Gene per Cell"])
            if "Median UMI per Cell" in kv:
                metrics["median_umi_per_cell"] = float(kv["Median UMI per Cell"])
            if "Sequencing Saturation" in kv:
                metrics["saturation"] = float(kv["Sequencing Saturation"])

            # Sequencing metrics
            if "Number of Reads" in kv:
                metrics["number_of_reads"] = int(kv["Number of Reads"])
            if "Reads With Valid Barcodes" in kv:
                metrics["valid_barcodes"] = float(kv["Reads With Valid Barcodes"])
            if "Q30 Bases in CB+UMI" in kv:
                metrics["q30_bases_barcode"] = float(kv["Q30 Bases in CB+UMI"])
            if "Q30 Bases in RNA read" in kv:
                metrics["q30_bases_rna_read"] = float(kv["Q30 Bases in RNA read"])

            # Mapping metrics
            if "Reads Mapped to Genome: Unique+Multiple" in kv:
                metrics["reads_mapped_genome"] = float(kv["Reads Mapped to Genome: Unique+Multiple"])
            if "Reads Mapped to Genome: Unique" in kv:
                metrics["reads_mapped_genome_unique"] = float(kv["Reads Mapped to Genome: Unique"])

            # Mean values and totals
            if "Mean Reads per Cell" in kv:
                metrics["mean_reads_per_cell"] = float(kv["Mean Reads per Cell"])
            if "Mean UMI per Cell" in kv:
                metrics["mean_umi_per_cell"] = float(kv["Mean UMI per Cell"])
            if "Mean Gene per Cell" in kv:
                metrics["mean_genes_per_cell"] = float(kv["Mean Gene per Cell"])
            if "Total Gene Detected" in kv:
                metrics["total_genes_detected"] = int(kv["Total Gene Detected"])
            if "UMIs in Cells" in kv:
                metrics["umis_in_cells"] = int(kv["UMIs in Cells"])

            logger.info("STARsolo metrics: %s", metrics)
        except Exception as e:
            logger.warning("STARsolo Summary.csv parsing failed: %s", e)

        return metrics

    @staticmethod
    def _read_multiqc_metrics(multiqc_json_text: str) -> dict:
        """Parse multiqc_data.json and extract aggregate FastQC metrics."""
        metrics: dict = {
            "total_sequences": None,
            "percent_duplicates": None,
            "percent_gc": None,
            "avg_sequence_length": None,
            "total_samples": None,
        }
        try:
            data = json.loads(multiqc_json_text)
            general_stats = data.get("report_general_stats_data", [])

            total_seqs = []
            dup_pcts = []
            gc_pcts = []
            seq_lengths = []

            for stats_section in general_stats:
                for _sample_name, sample_stats in stats_section.items():
                    # MultiQC JSON uses lowercase keys with underscores
                    for key in ("total_sequences", "Total Sequences"):
                        if key in sample_stats:
                            total_seqs.append(sample_stats[key])
                            break
                    for key in ("percent_duplicates", "total_deduplicated_percentage"):
                        if key in sample_stats:
                            val = sample_stats[key]
                            # total_deduplicated_percentage needs inversion
                            dup_pcts.append(100 - val if "deduplicated" in key else val)
                            break
                    for key in ("percent_gc", "%GC"):
                        if key in sample_stats:
                            gc_pcts.append(sample_stats[key])
                            break
                    if "avg_sequence_length" in sample_stats:
                        seq_lengths.append(sample_stats["avg_sequence_length"])

            if total_seqs:
                metrics["total_sequences"] = int(sum(total_seqs))
                metrics["total_samples"] = len(total_seqs)
            if dup_pcts:
                metrics["percent_duplicates"] = round(sum(dup_pcts) / len(dup_pcts), 1)
            if gc_pcts:
                metrics["percent_gc"] = round(sum(gc_pcts) / len(gc_pcts), 1)
            if seq_lengths:
                metrics["avg_sequence_length"] = round(sum(seq_lengths) / len(seq_lengths), 1)

        except Exception as e:
            logger.warning("MultiQC JSON parsing failed: %s", e)

        return metrics

    @staticmethod
    def _read_multiqc_general_stats(stats_tsv_text: str) -> dict:
        """Parse multiqc_general_stats.txt (TSV) as fallback."""
        import csv

        metrics: dict = {
            "total_sequences": None,
            "percent_duplicates": None,
            "percent_gc": None,
            "avg_sequence_length": None,
            "total_samples": None,
        }
        try:
            reader = csv.DictReader(stats_tsv_text.strip().splitlines(), delimiter="\t")
            total_seqs = []
            dup_pcts = []
            gc_pcts = []
            seq_lengths = []

            for row in reader:
                for key, val in row.items():
                    if not val or not key:
                        continue
                    k = key.lower()
                    try:
                        v = float(val)
                    except ValueError:
                        continue
                    if "total_sequences" in k or "total sequences" in k:
                        total_seqs.append(v)
                    if "deduplicated_percentage" in k:
                        dup_pcts.append(100 - v)
                    if "%gc" in k or "percent_gc" in k:
                        gc_pcts.append(v)
                    if "avg_sequence_length" in k:
                        seq_lengths.append(v)

            if total_seqs:
                metrics["total_sequences"] = int(sum(total_seqs))
                metrics["total_samples"] = len(total_seqs)
            if dup_pcts:
                metrics["percent_duplicates"] = round(sum(dup_pcts) / len(dup_pcts), 1)
            if gc_pcts:
                metrics["percent_gc"] = round(sum(gc_pcts) / len(gc_pcts), 1)
            if seq_lengths:
                metrics["avg_sequence_length"] = round(sum(seq_lengths) / len(seq_lengths), 1)

        except Exception as e:
            logger.warning("MultiQC general stats parsing failed: %s", e)

        return metrics

    @staticmethod
    def _build_barcode_rank_data(umi_counts: list[int], max_points: int = 500) -> list[list[int]]:
        """Build a barcode rank plot curve from sorted UMI counts.

        Returns a list of [rank, umi_count] pairs, downsampled via log-spaced
        indices when the input exceeds max_points.
        """
        if not umi_counts:
            return []

        n = len(umi_counts)
        if n <= max_points:
            return [[i + 1, c] for i, c in enumerate(umi_counts)]

        # Log-spaced indices to preserve detail in the knee region
        import math

        indices = sorted(
            set(
                [0]
                + [int(round(math.exp(i * math.log(n - 1) / (max_points - 2)))) for i in range(1, max_points - 1)]
                + [n - 1]
            )
        )
        return [[idx + 1, umi_counts[idx]] for idx in indices]

    @staticmethod
    def _extract_barcode_rank_from_mtx(mtx_text: str) -> list[list[int]]:
        """Parse a Market Exchange format sparse matrix and build barcode rank data.

        Sums values per column (barcode), sorts descending, then downsamples.
        """
        from collections import defaultdict

        col_sums: dict[int, int] = defaultdict(int)
        for line in mtx_text.strip().splitlines():
            if line.startswith("%"):
                continue
            parts = line.split()
            if len(parts) == 3:
                try:
                    col = int(parts[1])
                    val = int(float(parts[2]))
                    col_sums[col] += val
                except (ValueError, IndexError):
                    continue

        if not col_sums:
            return []

        sorted_counts = sorted(col_sums.values(), reverse=True)
        return QCDashboardService._build_barcode_rank_data(sorted_counts)

    @staticmethod
    def _read_umi_per_cell_sorted(text: str) -> list[list[int]]:
        """Parse UMIperCellSorted.txt (one UMI count per line, descending) into barcode rank data."""
        counts = []
        for line in text.strip().splitlines():
            line = line.strip()
            if line:
                try:
                    counts.append(int(line))
                except ValueError:
                    continue
        return QCDashboardService._build_barcode_rank_data(counts)

    @staticmethod
    def _read_multiqc_chart_data(multiqc_json_text: str) -> dict:
        """Extract structured chart data from multiqc_data.json for interactive rendering.

        Supports MultiQC v2 format where:
        - Bar charts use datasets[].cats[] with name/data_pct arrays
        - Line charts use datasets[].lines[] with name/pairs arrays

        Extracts:
        - star_alignment: STAR alignment categories (bar chart)
        - base_quality: per-base sequence quality (line chart, averaged across samples)
        - gc_content: GC content distribution (line chart, averaged across samples)
        - duplication: sequence duplication levels (line chart, averaged across samples)
        """
        chart_data: dict = {}
        try:
            data = json.loads(multiqc_json_text)
            plot_data = data.get("report_plot_data", {})

            # STAR alignment (bar chart with cats)
            star_plot = plot_data.get("star_alignment_plot")
            if star_plot:
                datasets = star_plot.get("datasets", [])
                if datasets:
                    ds = datasets[0]
                    # v2 format: cats[] with name and data_pct
                    cats = ds.get("cats", [])
                    if cats:
                        star_items = []
                        for cat in cats:
                            name = cat.get("name", "")
                            pct = cat.get("data_pct", [])
                            if pct:
                                star_items.append({"name": name, "value": round(pct[0], 2)})
                        if star_items:
                            chart_data["star_alignment"] = star_items
                    else:
                        # Fallback: older format with data[].name/data
                        entries = ds.get("data", [])
                        star_items = []
                        for entry in entries:
                            name = entry.get("name", "")
                            points = entry.get("data", [])
                            if points:
                                val = points[0].get("y", 0) if isinstance(points[0], dict) else points[0][1]
                                star_items.append({"name": name, "value": val})
                        if star_items:
                            chart_data["star_alignment"] = star_items

            # Helper: extract averaged line data from v2 lines[] format
            def _avg_lines(plot_key: str) -> list[list[float]] | None:
                plot = plot_data.get(plot_key)
                if not plot:
                    return None
                datasets_list = plot.get("datasets", [])
                if not datasets_list:
                    return None
                ds = datasets_list[0]
                lines = ds.get("lines", [])
                if lines:
                    # Average across all sample lines
                    all_x: dict[float, list[float]] = {}
                    for line in lines:
                        for x, y in line.get("pairs", []):
                            try:
                                xf = float(x)
                            except (ValueError, TypeError):
                                continue
                            try:
                                yf = float(y)
                            except (ValueError, TypeError):
                                continue
                            all_x.setdefault(xf, []).append(yf)
                    if all_x:
                        return [[x, round(sum(ys) / len(ys), 4)] for x, ys in sorted(all_x.items())]
                # Fallback: older format
                entries = ds.get("data", [])
                if entries:
                    points = entries[0].get("data", [])
                    if points:
                        return points
                return None

            # Per-base sequence quality
            bq_data = _avg_lines("fastqc_per_base_sequence_quality_plot")
            if bq_data:
                chart_data["base_quality"] = bq_data

            # GC content distribution (average samples, no theoretical in v2)
            gc_data = _avg_lines("fastqc_per_sequence_gc_content_plot")
            if gc_data:
                chart_data["gc_content"] = {"sample": gc_data}

            # Duplication levels
            dup_data = _avg_lines("fastqc_sequence_duplication_levels_plot")
            if dup_data:
                chart_data["duplication"] = dup_data

        except Exception as e:
            logger.warning("MultiQC chart data extraction failed: %s", e)

        return chart_data

    @staticmethod
    def _compute_quality_rating(metrics: dict) -> str:
        """Compute quality rating based on metrics thresholds."""
        mito = metrics.get("mito_pct_median")
        genes = metrics.get("median_genes_per_cell")
        reads = metrics.get("median_reads_per_cell")
        sat = metrics.get("saturation")
        mapping = metrics.get("reads_mapped_genome")
        q30_rna = metrics.get("q30_bases_rna_read")
        valid_bc = metrics.get("valid_barcodes")

        # Single-cell metrics -- rate based on what's available
        has_sc_metrics = genes is not None or mito is not None
        if has_sc_metrics:
            # Check for hard failures first
            if mito is not None and mito > 20:
                return "concerning"
            if mapping is not None and mapping < 0.5:
                return "concerning"

            # Full metrics available (after scanpy QC)
            if mito is not None and genes is not None:
                if mito < 5 and genes > 1000 and (reads is None or reads > 2000):
                    if sat is not None and sat > 0.7:
                        return "excellent"
                    return "good"
                if mito < 10 and genes > 500:
                    return "acceptable"
                return "acceptable"

            # STARsolo-level metrics (genes but no mito)
            if genes is not None and genes > 1000:
                excellent_signals = [
                    reads is not None and reads > 10000,
                    sat is not None and sat > 0.7,
                    mapping is not None and mapping > 0.9,
                    q30_rna is not None and q30_rna > 0.9,
                    valid_bc is not None and valid_bc > 0.95,
                ]
                good_count = sum(1 for s in excellent_signals if s)
                if good_count >= 4:
                    return "excellent"
                if reads is not None and reads > 10000 and sat is not None and sat > 0.5:
                    return "good"
                return "acceptable"
            if genes is not None and genes > 500:
                return "acceptable"
            if genes is not None:
                return "concerning"

            # Only mito available
            if mito is not None and mito > 20:
                return "concerning"
            return "acceptable"

        # Bulk/FastQC metrics
        dup = metrics.get("percent_duplicates")
        gc = metrics.get("percent_gc")
        total = metrics.get("total_sequences")
        if total is not None:
            if dup is not None and dup < 30 and gc is not None and 35 <= gc <= 65:
                return "good"
            if dup is not None and dup < 50:
                return "acceptable"
            return "acceptable" if dup is None else "concerning"

        # Only cell_count or no metrics at all
        cell_count = metrics.get("cell_count")
        if cell_count is not None and cell_count > 0:
            return "pending_review"

        return "concerning"

    @staticmethod
    def _generate_summary(metrics: dict) -> str:
        """Generate plain English summary from metrics."""
        parts = []

        cell_count = metrics.get("cell_count")
        if cell_count is not None:
            parts.append(f"This run produced **{cell_count:,} cells**")

        genes = metrics.get("median_genes_per_cell")
        if genes is not None:
            parts.append(f"with a median of **{genes:,.0f} genes per cell**")

        umi = metrics.get("median_umi_per_cell")
        if umi is not None:
            parts.append(f"and **{umi:,.0f} UMIs per cell**")

        # Bulk/FastQC metrics
        total_seqs = metrics.get("total_sequences")
        if total_seqs is not None:
            n_samples = metrics.get("total_samples", 0)
            parts.append(f"**{total_seqs:,} total sequences** across **{n_samples} samples**")

        avg_len = metrics.get("avg_sequence_length")
        if avg_len is not None:
            parts.append(f"with average read length **{avg_len:.0f} bp**")

        summary = " ".join(parts) + "." if parts else "No metrics available."

        num_reads = metrics.get("number_of_reads")
        if num_reads is not None:
            summary += f" **{num_reads:,} total reads**."

        reads = metrics.get("median_reads_per_cell")
        if reads is not None:
            summary += f" Median **{reads:,.0f} reads per cell**."

        # Mapping quality
        mapping = metrics.get("reads_mapped_genome")
        if mapping is not None:
            summary += f" **{mapping * 100:.1f}%** of reads mapped to genome."

        # Q30 quality
        q30_rna = metrics.get("q30_bases_rna_read")
        if q30_rna is not None:
            health = "good" if q30_rna >= 0.9 else "below 90% threshold"
            summary += f" Q30 bases in RNA read: **{q30_rna * 100:.1f}%** ({health})."

        # Note when mito % is missing (common before scanpy QC)
        mito_available = metrics.get("mito_pct_median") is not None
        genes = metrics.get("median_genes_per_cell")
        if genes is not None and not mito_available:
            summary += " Mitochondrial % and doublet scores require scanpy/scran QC preprocessing."

        mito = metrics.get("mito_pct_median")
        if mito is not None:
            health = "healthy, under 5% threshold" if mito < 5 else "elevated" if mito < 10 else "high"
            summary += f" Mitochondrial content is **{mito:.1f}%** ({health})."

        dup = metrics.get("percent_duplicates")
        if dup is not None:
            health = "low" if dup < 30 else "moderate" if dup < 50 else "high"
            summary += f" Duplication rate is **{dup:.1f}%** ({health})."

        gc = metrics.get("percent_gc")
        if gc is not None:
            summary += f" GC content is **{gc:.0f}%**."

        sat = metrics.get("saturation")
        if sat is not None:
            sat_pct = sat * 100
            summary += f" Sequencing saturation is **{sat_pct:.0f}%**"
            if sat < 0.8:
                summary += ", suggesting additional sequencing depth may improve gene detection."
            else:
                summary += "."

        quality = metrics.get("quality_rating", "concerning")
        summary += f" Overall quality: **{quality.capitalize()}**."

        return summary

    @staticmethod
    async def _get_results_bucket(session: AsyncSession) -> str | None:
        """Read results bucket name from platform_config.

        Checks results_bucket_name first, then falls back to deriving it
        from raw_bucket_name (bioaf-raw-X -> bioaf-results-X) since the
        raw bucket is populated by Terraform before results_bucket_name.
        """
        result = await session.execute(
            text("SELECT key, value FROM platform_config WHERE key IN ('results_bucket_name', 'raw_bucket_name')")
        )
        config = {r[0]: r[1] for r in result.fetchall()}

        results = config.get("results_bucket_name")
        if results and results != "null":
            return results

        # Derive from raw_bucket_name as fallback
        raw = config.get("raw_bucket_name", "")
        if raw and raw.startswith("bioaf-raw-"):
            return raw.replace("bioaf-raw-", "bioaf-results-", 1)

        return None

    @staticmethod
    async def _collect_plots(session: AsyncSession, org_id: int, run: PipelineRun) -> list[dict]:
        """Find pipeline-generated plot PNGs in GCS and register as file records."""
        plots_meta: list[dict] = []
        results_bucket = await QCDashboardService._get_results_bucket(session)
        if not results_bucket:
            return plots_meta

        try:
            from google.cloud import storage

            credentials = await GcsStorageService.get_credentials(session)
            client = storage.Client(credentials=credentials)
            bucket = client.bucket(results_bucket)
            prefix = f"experiments/{run.experiment_id}/pipeline-runs/{run.id}/"

            # Build an index of available PNG blobs in multiqc/multiqc_plots/png/
            # list_blobs returns blobs with metadata (including size) already populated
            plot_prefix = f"{prefix}multiqc/multiqc_plots/png/"
            available: dict[str, "storage.Blob"] = {}  # filename -> blob with metadata
            for blob in bucket.list_blobs(prefix=plot_prefix):
                if blob.name.endswith(".png"):
                    filename = blob.name.rsplit("/", 1)[-1]
                    available[filename] = blob

            for png_name, title, plot_type in QCDashboardService._MULTIQC_PLOTS:
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
