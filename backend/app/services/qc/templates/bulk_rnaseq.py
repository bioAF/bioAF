"""Bulk RNA-seq QC template.

Built-in template for nf-core/rnaseq-style runs. Reads MultiQC aggregate stats
(FastQC raw reads + STAR alignment) for per-sample QC. Unlike the scRNA-seq
template (STARsolo/h5ad/10x outputs), a bulk run emits the standard nf-core
MultiQC report, so metrics come from ``report_saved_raw_data`` in
``multiqc_data.json``.

Counts are aggregated per-sample by MEAN (read depth ~ mean reads/sample), which
is the basis a paper's per-sample claim is compared against (lit_validation
calibration, 2026-07-08). Before this template had a real extractor, bulk runs
reused the scRNA-seq extractor and produced an all-null dashboard, which starved
the lit_validation classifier into an inconclusive verdict on every bulk paper.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline_run import PipelineRun
from app.services.qc.extractors.gcs_helpers import get_results_bucket
from app.services.qc.multiqc_registry import read_depth_and_samples

logger = logging.getLogger("bioaf.qc.bulk_rnaseq")

EMPTY_METRICS: dict[str, Any] = {
    "total_samples": None,
    "total_sequences": None,
    "avg_sequence_length": None,
    "percent_duplicates": None,
    "percent_gc": None,
    "reads_mapped_genome": None,
    "reads_mapped_genome_unique": None,
}

# The standard nf-core MultiQC PNG set (identical to scRNA-seq); the dashboard
# service collects whichever of these the run produced.
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
        "template": "bulk_rnaseq",
        "sections": [
            {
                "id": "hero",
                "layout": "hero",
                "metrics": ["total_samples", "total_sequences", "percent_duplicates", "percent_gc"],
            },
            {
                "id": "sequencing",
                "title": "Sequencing",
                "layout": "grid",
                "metrics": [
                    "total_samples",
                    "total_sequences",
                    "avg_sequence_length",
                    "percent_duplicates",
                    "percent_gc",
                ],
            },
            {
                "id": "mapping",
                "title": "Mapping",
                "layout": "grid",
                "metrics": ["reads_mapped_genome", "reads_mapped_genome_unique"],
            },
        ],
        "metrics": {
            "total_samples": {"label": "Samples", "format": "integer"},
            # Aggregated per-sample by mean (not a grand total), matching the
            # per-sample basis the lit_validation comparison uses.
            "total_sequences": {"label": "Mean Reads / Sample", "format": "integer"},
            "avg_sequence_length": {"label": "Avg Read Length", "format": "bp"},
            "percent_duplicates": {
                "label": "Duplicates",
                "format": "percent_pct",
                "thresholds": {"good": "<30", "warn": "<50"},
            },
            "percent_gc": {"label": "GC Content", "format": "percent_pct"},
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
        },
        "charts": [
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
    dup = metrics.get("percent_duplicates")
    gc = metrics.get("percent_gc")
    total = metrics.get("total_sequences")
    mapping = metrics.get("reads_mapped_genome")

    if total is None and dup is None and mapping is None:
        return "pending_review"

    if mapping is not None and mapping < 0.5:
        return "concerning"

    if total is not None:
        if dup is not None and dup < 30 and gc is not None and 35 <= gc <= 65:
            if mapping is None or mapping >= 0.7:
                return "good"
            return "acceptable"
        if dup is not None and dup < 50:
            return "acceptable"
        return "acceptable" if dup is None else "concerning"

    return "pending_review"


# ---- MultiQC parsing (pure) ----


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").strip())
        except ValueError:
            return None
    return None


def _field(sample: dict, *keys: str) -> float | None:
    """First numeric value among the given keys for one sample record."""
    for k in keys:
        if k in sample:
            n = _num(sample[k])
            if n is not None:
                return n
    return None


def _numbers(section: dict, *keys: str) -> list[float]:
    out: list[float] = []
    for v in section.values():
        if isinstance(v, dict):
            n = _field(v, *keys)
            if n is not None:
                out.append(n)
    return out


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def _pick_raw_fastqc(raw: dict) -> dict:
    """nf-core/rnaseq runs FastQC on raw + trimmed reads; MultiQC stores them as
    multiqc_fastqc / multiqc_fastqc_1. Trimming only removes reads, so the RAW
    section is the one with the higher total read count -- identify it by that
    physical invariant, not by the section suffix (whose ordering is not
    guaranteed). Read depth is a pre-trim (raw) quantity."""
    sections = [v for k, v in raw.items() if isinstance(v, dict) and k.startswith("multiqc_fastqc")]
    best: dict = {}
    best_total = -1.0
    for sec in sections:
        total = sum(_numbers(sec, "Total Sequences", "total_sequences"))
        if total > best_total:
            best_total = total
            best = sec
    return best


def read_multiqc_metrics(multiqc_json_text: str) -> dict[str, Any]:
    """Parse per-sample FastQC (raw) + STAR from a bulk-RNA-seq multiqc_data.json,
    aggregating per-sample by mean. STAR percentages are converted to the 0-1
    fractions the controlled QC vocabulary uses."""
    try:
        data = json.loads(multiqc_json_text)
    except Exception as e:
        logger.warning("bulk MultiQC JSON parsing failed: %s", e)
        return dict(EMPTY_METRICS)

    metrics = dict(EMPTY_METRICS)
    raw = data.get("report_saved_raw_data") or {}

    fastqc = _pick_raw_fastqc(raw)
    if fastqc:
        total_seqs = _numbers(fastqc, "Total Sequences", "total_sequences")
        gc = _numbers(fastqc, "%GC", "percent_gc")
        dedup = _numbers(fastqc, "total_deduplicated_percentage")
        lengths = _numbers(fastqc, "avg_sequence_length")
        if total_seqs:
            # Depth and sample count are derived per SAMPLE by the shared helper
            # (aligner roster, raw FastQC counts, lanes added and mates collapsed)
            # so a paired-end or multi-lane run is not distorted by counting files.
            depth, total_samples, _sources = read_depth_and_samples(data)
            if depth is not None:
                metrics["total_sequences"] = depth
            if total_samples is not None:
                metrics["total_samples"] = total_samples
        if gc:
            metrics["percent_gc"] = round(_mean(gc), 1)
        if dedup:
            # FastQC reports the % remaining after dedup; duplication is the complement.
            metrics["percent_duplicates"] = round(_mean([100.0 - d for d in dedup]), 1)
        if lengths:
            metrics["avg_sequence_length"] = round(_mean(lengths), 1)

    star = raw.get("multiqc_star") or {}
    uniq_fracs: list[float] = []
    mapped_fracs: list[float] = []
    for v in star.values():
        if not isinstance(v, dict):
            continue
        up = _field(v, "uniquely_mapped_percent")
        if up is None:
            continue
        mp = _field(v, "multimapped_percent") or 0.0
        uniq_fracs.append(up / 100.0)
        # "Mapped to genome" = uniquely + multi-mapped (excludes too-many + unmapped).
        mapped_fracs.append((up + mp) / 100.0)
    if uniq_fracs:
        metrics["reads_mapped_genome_unique"] = round(_mean(uniq_fracs), 4)
        metrics["reads_mapped_genome"] = round(_mean(mapped_fracs), 4)

    return metrics


async def extract(
    session: AsyncSession,
    run: PipelineRun,
    *,
    skip_cache: bool = False,
    results_bucket: str | None = None,
) -> dict[str, Any]:
    """Extract bulk RNA-seq QC metrics from GCS for the given run.

    Checks for a cached qc_metrics.json first; on miss, locates the run's
    multiqc_data.json (nf-core/rnaseq writes it under multiqc/<aligner>/), parses
    it, and writes the cache back. results_bucket can be passed to let callers
    mock it out; otherwise it is resolved from platform_config.
    """
    if results_bucket is None:
        results_bucket = await get_results_bucket(session)
    if not results_bucket:
        logger.warning("No results bucket configured, cannot extract metrics")
        return dict(EMPTY_METRICS)

    try:
        from app.adapters.models import StorageObjectNotFound
        from app.adapters.registry import get_storage_adapter

        adapter = get_storage_adapter()
        prefix = f"experiments/{run.experiment_id}/pipeline-runs/{run.id}/"
        base = adapter.build_uri(results_bucket, "")

        cache_uri = f"{base}{prefix}qc_metrics.json"
        if not skip_cache:
            try:
                cached = json.loads(await adapter.read_text(cache_uri))
                logger.info("Using cached qc_metrics.json for run %d", run.id)
                return cached
            except StorageObjectNotFound:
                pass

        # nf-core/rnaseq nests the MultiQC report under the aligner dir
        # (multiqc/star_salmon/... by default); locate it anywhere under multiqc/.
        multiqc_objs = await adapter.list_objects(f"{base}{prefix}multiqc/")
        json_uris = [o.storage_uri for o in multiqc_objs if o.storage_uri.endswith("multiqc_data.json")]
        multiqc_uri = next((u for u in json_uris if "star_salmon" in u), None) or (json_uris[0] if json_uris else None)

        metrics = dict(EMPTY_METRICS)
        if multiqc_uri:
            logger.info("Found bulk multiqc_data.json for run %d at %s", run.id, multiqc_uri[len(base) :])
            text = await adapter.read_text(multiqc_uri)
            metrics = read_multiqc_metrics(text)
            try:
                # Generic MultiQC report_plot_data parser (shared with scRNA-seq).
                from app.services.qc.templates.scrnaseq import read_multiqc_chart_data

                chart_data = read_multiqc_chart_data(text)
                if chart_data:
                    metrics["chart_data"] = chart_data
            except Exception as e:
                logger.warning("bulk chart data extraction failed for run %d: %s", run.id, e)
        else:
            logger.info("No multiqc_data.json found under multiqc/ for run %d", run.id)

        has_any = any(v is not None for k, v in metrics.items() if k != "chart_data")
        if not has_any:
            logger.info("No bulk metrics found for run %d", run.id)
            return dict(EMPTY_METRICS)

        await adapter.write_text(cache_uri, json.dumps(metrics, indent=2), content_type="application/json")
        logger.info("Wrote qc_metrics.json cache for run %d", run.id)
        return metrics

    except Exception as e:
        logger.warning("Bulk metric extraction from storage failed for run %d: %s", run.id, e)
        return dict(EMPTY_METRICS)


def generate_summary(metrics: dict[str, Any]) -> str:
    """Plain-English summary for a bulk RNA-seq run."""
    total = metrics.get("total_sequences")
    n = metrics.get("total_samples")

    if total is not None and n:
        summary = f"This bulk RNA-seq run analyzed **{n} samples** with a mean of **{total:,} reads per sample**."
    elif total is not None:
        summary = f"This bulk RNA-seq run had a mean of **{total:,} reads per sample**."
    elif n:
        summary = f"This bulk RNA-seq run analyzed **{n} samples**."
    else:
        summary = "No metrics available."

    mapping = metrics.get("reads_mapped_genome")
    if mapping is not None:
        summary += f" **{mapping * 100:.1f}%** of reads mapped to the genome"
        uniq = metrics.get("reads_mapped_genome_unique")
        if uniq is not None:
            summary += f" (**{uniq * 100:.1f}%** uniquely)"
        summary += "."

    dup = metrics.get("percent_duplicates")
    if dup is not None:
        health = "low" if dup < 30 else "moderate" if dup < 50 else "high"
        summary += f" Duplication rate is **{dup:.1f}%** ({health})."

    gc = metrics.get("percent_gc")
    if gc is not None:
        summary += f" GC content is **{gc:.0f}%**."

    alen = metrics.get("avg_sequence_length")
    if alen is not None:
        summary += f" Mean read length **{alen:.0f} bp**."

    quality = metrics.get("quality_rating", "pending_review")
    summary += f" Overall quality: **{quality.capitalize()}**."
    return summary


__all__ = [
    "render_config",
    "compute_quality",
    "read_multiqc_metrics",
    "extract",
    "generate_summary",
    "EMPTY_METRICS",
    "MULTIQC_PLOTS",
]
