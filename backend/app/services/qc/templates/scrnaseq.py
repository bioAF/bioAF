"""scRNA-seq QC template.

Lifts the existing scRNA-seq dashboard logic from QCDashboardService into a
self-contained template. Render config, quality rating, GCS extractor, and
summary generator all live here. The dashboard service dispatches into this
module via the template registry.
"""

from __future__ import annotations

import json
import logging
import math
import tempfile
from collections import defaultdict
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline_run import PipelineRun
from app.services.gcs_storage import GcsStorageService
from app.services.qc.extractors.gcs_helpers import get_results_bucket

logger = logging.getLogger("bioaf.qc.scrnaseq")

EMPTY_METRICS: dict[str, Any] = {
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

MULTIQC_PLOTS: list[tuple[str, str, str]] = [
    ("star_alignment_plot-pct.png", "STAR Alignment", "star_alignment"),
    ("fastqc_per_base_sequence_quality_plot.png", "Per-Base Sequence Quality", "base_quality"),
    ("fastqc_per_sequence_gc_content_plot_Percentages.png", "GC Content Distribution", "gc_content"),
    ("fastqc_sequence_duplication_levels_plot.png", "Sequence Duplication Levels", "duplication"),
    ("fastqc_sequence_counts_plot-cnt.png", "Sequence Counts", "seq_counts"),
    ("general_stats_table.png", "General Statistics", "general_stats"),
]


def render_config() -> dict:
    return {
        "template": "scrnaseq",
        "sections": [
            {
                "id": "hero",
                "layout": "hero",
                "metrics": [
                    "cell_count",
                    "mean_reads_per_cell",
                    "median_genes_per_cell",
                    "median_umi_per_cell",
                ],
            },
            {
                "id": "cells",
                "title": "Cells",
                "layout": "grid",
                "metrics": [
                    "cell_count",
                    "total_genes_detected",
                    "median_genes_per_cell",
                    "mean_genes_per_cell",
                    "median_umi_per_cell",
                    "mean_umi_per_cell",
                    "umis_in_cells",
                    "mito_pct_median",
                    "doublet_score_median",
                ],
            },
            {
                "id": "sequencing",
                "title": "Sequencing",
                "layout": "grid",
                "metrics": [
                    "number_of_reads",
                    "median_reads_per_cell",
                    "mean_reads_per_cell",
                    "saturation",
                    "valid_barcodes",
                    "q30_bases_barcode",
                    "q30_bases_rna_read",
                ],
            },
            {
                "id": "mapping",
                "title": "Mapping",
                "layout": "grid",
                "metrics": [
                    "reads_mapped_genome",
                    "reads_mapped_genome_unique",
                ],
            },
            {
                "id": "bulk",
                "title": "Per-Sample QC (FastQC aggregate)",
                "layout": "grid",
                "metrics": [
                    "total_sequences",
                    "total_samples",
                    "percent_duplicates",
                    "percent_gc",
                    "avg_sequence_length",
                ],
            },
        ],
        "metrics": {
            "cell_count": {"label": "Estimated Number of Cells", "format": "integer"},
            "total_genes_detected": {"label": "Total Genes Detected", "format": "integer"},
            "median_genes_per_cell": {"label": "Median Genes per Cell", "format": "integer"},
            "mean_genes_per_cell": {"label": "Mean Genes per Cell", "format": "integer"},
            "median_umi_per_cell": {"label": "Median UMI per Cell", "format": "integer"},
            "mean_umi_per_cell": {"label": "Mean UMI per Cell", "format": "integer"},
            "umis_in_cells": {"label": "UMIs in Cells", "format": "integer"},
            "mito_pct_median": {
                "label": "Median Mitochondrial %",
                "format": "decimal",
                "thresholds": {"good": "<5", "warn": "<10"},
            },
            "doublet_score_median": {"label": "Median Doublet Score", "format": "decimal"},
            "number_of_reads": {"label": "Number of Reads", "format": "integer"},
            "median_reads_per_cell": {"label": "Median Reads per Cell", "format": "integer"},
            "mean_reads_per_cell": {"label": "Mean Reads per Cell", "format": "integer"},
            "saturation": {
                "label": "Sequencing Saturation",
                "format": "percent_decimal",
                "thresholds": {"good": ">=0.8", "warn": ">=0.5"},
            },
            "valid_barcodes": {
                "label": "Reads With Valid Barcodes",
                "format": "percent_decimal",
                "thresholds": {"good": ">=0.95", "warn": ">=0.85"},
            },
            "q30_bases_barcode": {
                "label": "Q30 Bases in CB+UMI",
                "format": "percent_decimal",
                "thresholds": {"good": ">=0.9", "warn": ">=0.8"},
            },
            "q30_bases_rna_read": {
                "label": "Q30 Bases in RNA Read",
                "format": "percent_decimal",
                "thresholds": {"good": ">=0.9", "warn": ">=0.8"},
            },
            "reads_mapped_genome": {
                "label": "Reads Mapped to Genome",
                "format": "percent_decimal",
                "thresholds": {"good": ">=0.9", "warn": ">=0.5"},
            },
            "reads_mapped_genome_unique": {
                "label": "Reads Mapped to Genome (Unique)",
                "format": "percent_decimal",
                "thresholds": {"good": ">=0.7", "warn": ">=0.5"},
            },
            "total_sequences": {"label": "Total Sequences", "format": "integer"},
            "total_samples": {"label": "Samples", "format": "integer"},
            "percent_duplicates": {
                "label": "Duplicates",
                "format": "percent_pct",
                "thresholds": {"good": "<30", "warn": "<50"},
            },
            "percent_gc": {"label": "GC Content", "format": "percent_pct"},
            "avg_sequence_length": {"label": "Avg Read Length", "format": "bp"},
        },
        "charts": [
            {"type": "barcode_rank", "metric_key": "barcode_rank_data", "title": "Barcode Rank"},
            {"type": "star_alignment", "metric_key": "chart_data.star_alignment", "title": "STAR Alignment"},
            {"type": "base_quality", "metric_key": "chart_data.base_quality", "title": "Per-Base Sequence Quality"},
            {"type": "gc_content", "metric_key": "chart_data.gc_content", "title": "GC Content Distribution"},
            {"type": "duplication", "metric_key": "chart_data.duplication", "title": "Sequence Duplication Levels"},
        ],
        "plots": [
            {
                "file_glob": "multiqc/multiqc_plots/png/star_alignment_plot-pct.png",
                "title": "STAR Alignment",
                "type": "star_alignment",
            },
            {
                "file_glob": "multiqc/multiqc_plots/png/fastqc_per_base_sequence_quality_plot.png",
                "title": "Per-Base Sequence Quality",
                "type": "base_quality",
            },
            {
                "file_glob": "multiqc/multiqc_plots/png/fastqc_per_sequence_gc_content_plot_Percentages.png",
                "title": "GC Content Distribution",
                "type": "gc_content",
            },
            {
                "file_glob": "multiqc/multiqc_plots/png/fastqc_sequence_duplication_levels_plot.png",
                "title": "Sequence Duplication Levels",
                "type": "duplication",
            },
            {
                "file_glob": "multiqc/multiqc_plots/png/fastqc_sequence_counts_plot-cnt.png",
                "title": "Sequence Counts",
                "type": "seq_counts",
            },
            {
                "file_glob": "multiqc/multiqc_plots/png/general_stats_table.png",
                "title": "General Statistics",
                "type": "general_stats",
            },
        ],
    }


def compute_quality(metrics: dict[str, Any]) -> str:
    """Mirrors QCDashboardService._compute_quality_rating for scRNA-seq.

    Kept verbatim so existing dashboards rate identically after migration.
    """
    mito = metrics.get("mito_pct_median")
    genes = metrics.get("median_genes_per_cell")
    reads = metrics.get("median_reads_per_cell")
    sat = metrics.get("saturation")
    mapping = metrics.get("reads_mapped_genome")
    q30_rna = metrics.get("q30_bases_rna_read")
    valid_bc = metrics.get("valid_barcodes")

    has_sc_metrics = genes is not None or mito is not None
    if has_sc_metrics:
        if mito is not None and mito > 20:
            return "concerning"
        if mapping is not None and mapping < 0.5:
            return "concerning"

        if mito is not None and genes is not None:
            if mito < 5 and genes > 1000 and (reads is None or reads > 2000):
                if sat is not None and sat > 0.7:
                    return "excellent"
                return "good"
            if mito < 10 and genes > 500:
                return "acceptable"
            return "acceptable"

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

        if mito is not None and mito > 20:
            return "concerning"
        return "acceptable"

    dup = metrics.get("percent_duplicates")
    gc = metrics.get("percent_gc")
    total = metrics.get("total_sequences")
    if total is not None:
        if dup is not None and dup < 30 and gc is not None and 35 <= gc <= 65:
            return "good"
        if dup is not None and dup < 50:
            return "acceptable"
        return "acceptable" if dup is None else "concerning"

    cell_count = metrics.get("cell_count")
    if cell_count is not None and cell_count > 0:
        return "pending_review"

    return "concerning"


def read_h5ad_metrics(path: str) -> dict[str, Any]:
    metrics: dict[str, Any] = {
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

        for col in ("n_genes", "n_genes_by_counts", "genes_detected", "nFeature_RNA", "n_features"):
            if col in obs_cols:
                metrics["median_genes_per_cell"] = float(np.median(adata.obs[col]))
                break

        for col in ("total_counts", "nCount_RNA", "n_counts", "total_umi"):
            if col in obs_cols:
                metrics["median_umi_per_cell"] = float(np.median(adata.obs[col]))
                break

        for col in ("pct_counts_mt", "pct_counts_mito", "percent.mt", "percent_mito", "mito_pct"):
            if col in obs_cols:
                metrics["mito_pct_median"] = float(np.median(adata.obs[col]))
                break

        for col in ("doublet_score", "scrublet_score", "doublet_scores"):
            if col in obs_cols:
                metrics["doublet_score_median"] = float(np.median(adata.obs[col]))
                break

    except ImportError:
        logger.warning("anndata not installed, cannot extract h5ad metrics")
    except Exception as e:
        logger.warning("h5ad metric extraction failed: %s", e)

    return metrics


def read_starsolo_summary(summary_csv_text: str) -> dict[str, Any]:
    metrics: dict[str, Any] = {
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
        kv: dict[str, str] = {}
        for line in summary_csv_text.strip().splitlines():
            if "," in line:
                key, val = line.split(",", 1)
                kv[key.strip()] = val.strip()

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

        if "Number of Reads" in kv:
            metrics["number_of_reads"] = int(kv["Number of Reads"])
        if "Reads With Valid Barcodes" in kv:
            metrics["valid_barcodes"] = float(kv["Reads With Valid Barcodes"])
        if "Q30 Bases in CB+UMI" in kv:
            metrics["q30_bases_barcode"] = float(kv["Q30 Bases in CB+UMI"])
        if "Q30 Bases in RNA read" in kv:
            metrics["q30_bases_rna_read"] = float(kv["Q30 Bases in RNA read"])

        if "Reads Mapped to Genome: Unique+Multiple" in kv:
            metrics["reads_mapped_genome"] = float(kv["Reads Mapped to Genome: Unique+Multiple"])
        if "Reads Mapped to Genome: Unique" in kv:
            metrics["reads_mapped_genome_unique"] = float(kv["Reads Mapped to Genome: Unique"])

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


def read_multiqc_metrics(multiqc_json_text: str) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "total_sequences": None,
        "percent_duplicates": None,
        "percent_gc": None,
        "avg_sequence_length": None,
        "total_samples": None,
    }
    try:
        data = json.loads(multiqc_json_text)
        general_stats = data.get("report_general_stats_data", [])

        total_seqs: list[float] = []
        dup_pcts: list[float] = []
        gc_pcts: list[float] = []
        seq_lengths: list[float] = []

        for stats_section in general_stats:
            for _sample_name, sample_stats in stats_section.items():
                for key in ("total_sequences", "Total Sequences"):
                    if key in sample_stats:
                        total_seqs.append(sample_stats[key])
                        break
                for key in ("percent_duplicates", "total_deduplicated_percentage"):
                    if key in sample_stats:
                        val = sample_stats[key]
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


def read_multiqc_chart_data(multiqc_json_text: str) -> dict[str, Any]:
    chart_data: dict[str, Any] = {}
    try:
        data = json.loads(multiqc_json_text)
        plot_data = data.get("report_plot_data", {})

        star_plot = plot_data.get("star_alignment_plot")
        if star_plot:
            datasets = star_plot.get("datasets", [])
            if datasets:
                ds = datasets[0]
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
            entries = ds.get("data", [])
            if entries:
                points = entries[0].get("data", [])
                if points:
                    return points
            return None

        bq_data = _avg_lines("fastqc_per_base_sequence_quality_plot")
        if bq_data:
            chart_data["base_quality"] = bq_data

        gc_data = _avg_lines("fastqc_per_sequence_gc_content_plot")
        if gc_data:
            chart_data["gc_content"] = {"sample": gc_data}

        dup_data = _avg_lines("fastqc_sequence_duplication_levels_plot")
        if dup_data:
            chart_data["duplication"] = dup_data

    except Exception as e:
        logger.warning("MultiQC chart data extraction failed: %s", e)

    return chart_data


def build_barcode_rank_data(umi_counts: list[int], max_points: int = 500) -> list[list[int]]:
    if not umi_counts:
        return []

    n = len(umi_counts)
    if n <= max_points:
        return [[i + 1, c] for i, c in enumerate(umi_counts)]

    indices = sorted(
        set(
            [0]
            + [int(round(math.exp(i * math.log(n - 1) / (max_points - 2)))) for i in range(1, max_points - 1)]
            + [n - 1]
        )
    )
    return [[idx + 1, umi_counts[idx]] for idx in indices]


def extract_barcode_rank_from_mtx(mtx_text: str) -> list[list[int]]:
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
    return build_barcode_rank_data(sorted_counts)


def read_umi_per_cell_sorted(text: str) -> list[list[int]]:
    counts: list[int] = []
    for line in text.strip().splitlines():
        line = line.strip()
        if line:
            try:
                counts.append(int(line))
            except ValueError:
                continue
    return build_barcode_rank_data(counts)


async def extract(
    session: AsyncSession,
    run: PipelineRun,
    *,
    skip_cache: bool = False,
    results_bucket: str | None = None,
) -> dict[str, Any]:
    """Extract scRNA-seq QC metrics from GCS for the given run.

    Checks for a cached qc_metrics.json first; on miss, layers metrics from
    STARsolo Summary.csv -> h5ad -> MultiQC, then writes the cache back.
    The results_bucket can be passed explicitly to let the caller mock it
    out; otherwise it is resolved from platform_config.
    """
    if results_bucket is None:
        results_bucket = await get_results_bucket(session)
    if not results_bucket:
        logger.warning("No results bucket configured, cannot extract metrics")
        return dict(EMPTY_METRICS)

    logger.info(
        "Looking for metrics in gs://%s/experiments/%s/pipeline-runs/%s/",
        results_bucket,
        run.experiment_id,
        run.id,
    )

    credentials = await GcsStorageService.get_credentials(session)

    try:
        from google.cloud import storage

        client = storage.Client(credentials=credentials)
        bucket = client.bucket(results_bucket)
        prefix = f"experiments/{run.experiment_id}/pipeline-runs/{run.id}/"

        blobs_at_prefix = [b.name for b in bucket.list_blobs(prefix=prefix, max_results=20)]
        logger.info("Files at prefix for run %d: %s", run.id, blobs_at_prefix)

        cache_blob = bucket.blob(f"{prefix}qc_metrics.json")
        if not skip_cache and cache_blob.exists():
            cached = json.loads(cache_blob.download_as_text())
            logger.info("Using cached qc_metrics.json for run %d", run.id)
            return cached

        metrics = dict(EMPTY_METRICS)

        starsolo_blob = None
        for blob in bucket.list_blobs(prefix=f"{prefix}star/"):
            if blob.name.endswith("Solo.out/Gene/Summary.csv"):
                starsolo_blob = blob
                break

        if starsolo_blob:
            logger.info("Found STARsolo Summary.csv for run %d", run.id)
            summary_text = starsolo_blob.download_as_text()
            starsolo_metrics = read_starsolo_summary(summary_text)
            for k, v in starsolo_metrics.items():
                if v is not None:
                    metrics[k] = v

        if metrics["cell_count"] is None:
            h5ad_blob = None
            for blob in bucket.list_blobs(prefix=prefix):
                if blob.name.endswith(".h5ad"):
                    h5ad_blob = blob
                    break

            if h5ad_blob:
                with tempfile.NamedTemporaryFile(suffix=".h5ad") as tmp:
                    h5ad_blob.download_to_filename(tmp.name)
                    h5ad_metrics = read_h5ad_metrics(tmp.name)
                    for k, v in h5ad_metrics.items():
                        if v is not None and metrics.get(k) is None:
                            metrics[k] = v

        multiqc_blob = None
        for blob in bucket.list_blobs(prefix=f"{prefix}multiqc/multiqc_data/"):
            if blob.name.endswith("multiqc_data.json"):
                multiqc_blob = blob
                break

        if multiqc_blob:
            logger.info("Found multiqc_data.json for run %d", run.id)
            multiqc_text = multiqc_blob.download_as_text()
            multiqc_metrics = read_multiqc_metrics(multiqc_text)
            for k, v in multiqc_metrics.items():
                if v is not None and metrics.get(k) is None:
                    metrics[k] = v

            chart_data = read_multiqc_chart_data(multiqc_text)
            if chart_data:
                metrics["chart_data"] = chart_data

        umi_sorted_blob = None
        for blob in bucket.list_blobs(prefix=f"{prefix}star/"):
            if blob.name.endswith("Solo.out/Gene/UMIperCellSorted.txt"):
                umi_sorted_blob = blob
                break

        if umi_sorted_blob:
            logger.info("Found UMIperCellSorted.txt for barcode rank plot, run %d", run.id)
            try:
                barcode_rank = read_umi_per_cell_sorted(umi_sorted_blob.download_as_text())
                if barcode_rank:
                    metrics["barcode_rank_data"] = barcode_rank
            except Exception as e:
                logger.warning("Barcode rank extraction failed for run %d: %s", run.id, e)
        else:
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
                    barcode_rank = extract_barcode_rank_from_mtx(raw_matrix_blob.download_as_text())
                    if barcode_rank:
                        metrics["barcode_rank_data"] = barcode_rank
                except Exception as e:
                    logger.warning("Barcode rank extraction failed for run %d: %s", run.id, e)

        has_any = any(v is not None for k, v in metrics.items() if k not in ("chart_data", "barcode_rank_data"))
        if not has_any:
            logger.info("No metrics found for run %d from any source", run.id)
            return dict(EMPTY_METRICS)

        cache_upload_blob = bucket.blob(f"{prefix}qc_metrics.json")
        cache_upload_blob.upload_from_string(
            json.dumps(metrics, indent=2),
            content_type="application/json",
        )
        logger.info("Wrote qc_metrics.json cache for run %d", run.id)

        return metrics

    except Exception as e:
        logger.warning("Metric extraction from GCS failed for run %d: %s", run.id, e)
        return dict(EMPTY_METRICS)


def generate_summary(metrics: dict[str, Any]) -> str:
    """Generate plain English summary from scRNA-seq metrics."""
    parts: list[str] = []

    cell_count = metrics.get("cell_count")
    if cell_count is not None:
        parts.append(f"This run produced **{cell_count:,} cells**")

    genes = metrics.get("median_genes_per_cell")
    if genes is not None:
        parts.append(f"with a median of **{genes:,.0f} genes per cell**")

    umi = metrics.get("median_umi_per_cell")
    if umi is not None:
        parts.append(f"and **{umi:,.0f} UMIs per cell**")

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

    mapping = metrics.get("reads_mapped_genome")
    if mapping is not None:
        summary += f" **{mapping * 100:.1f}%** of reads mapped to genome."

    q30_rna = metrics.get("q30_bases_rna_read")
    if q30_rna is not None:
        health = "good" if q30_rna >= 0.9 else "below 90% threshold"
        summary += f" Q30 bases in RNA read: **{q30_rna * 100:.1f}%** ({health})."

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


__all__ = [
    "render_config",
    "compute_quality",
    "extract",
    "generate_summary",
    "read_h5ad_metrics",
    "read_starsolo_summary",
    "read_multiqc_metrics",
    "read_multiqc_chart_data",
    "read_umi_per_cell_sorted",
    "build_barcode_rank_data",
    "extract_barcode_rank_from_mtx",
    "MULTIQC_PLOTS",
    "EMPTY_METRICS",
]
