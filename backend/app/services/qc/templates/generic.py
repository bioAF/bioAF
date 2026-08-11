"""Generic QC template: the fallback for any pipeline without a tailored one.

Every nf-core analysis pipeline ends in MultiQC, so a pipeline type nobody has
written an extractor for is not actually opaque. This template locates whatever
``multiqc_data.json`` the run wrote, maps its module sections onto the controlled
QC vocabulary via ``qc.multiqc_registry``, and keeps everything else it found
under ``additional_metrics``.

What it deliberately does NOT do: invent a quality verdict. It has no
type-specific thresholds, so quality stays ``pending_review`` and the numbers are
presented for a human to read. Type-specific judgement belongs in a tailored
template.

Every metric it emits is a QC floor, never a finding (spec-06): a pipeline type
covered only by this template can support an `inconclusive` verdict in
lit_validation, never a `validated` one.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline_run import PipelineRun
from app.services.qc.extractors.gcs_helpers import get_results_bucket
from app.services.qc.multiqc_registry import EMPTY_METRICS as _REGISTRY_EMPTY
from app.services.qc.multiqc_registry import parse_multiqc_metrics

logger = logging.getLogger("bioaf.qc.generic")

EMPTY_METRICS: dict[str, Any] = {**_REGISTRY_EMPTY, "metric_sources": {}, "additional_metrics": {}}

# Only the pipeline-agnostic plots. Anything aligner- or assay-specific belongs
# to a tailored template; listing it here would advertise plots that a given
# pipeline type never produces.
MULTIQC_PLOTS: list[tuple[str, str, str]] = [
    ("fastqc_per_base_sequence_quality_plot.png", "Per-Base Sequence Quality", "base_quality"),
    ("fastqc_per_sequence_gc_content_plot_Percentages.png", "GC Content Distribution", "gc_content"),
    ("fastqc_sequence_duplication_levels_plot.png", "Sequence Duplication Levels", "duplication"),
    ("fastqc_sequence_counts_plot-cnt.png", "Sequence Counts", "seq_counts"),
    ("general_stats_table.png", "General Statistics", "general_stats"),
]


def render_config() -> dict:
    return {
        "template": "generic",
        "sections": [
            {
                "id": "summary",
                "title": "Sequencing Summary",
                "metrics": ["total_samples", "total_sequences", "avg_sequence_length", "percent_gc"],
            },
            {
                "id": "alignment",
                "title": "Alignment",
                "metrics": ["reads_mapped_genome", "reads_mapped_genome_unique", "percent_duplicates"],
            },
            {
                "id": "additional",
                "title": "Additional Metrics",
                "description": (
                    "Everything else this pipeline's MultiQC report carried, named by the tool that "
                    "reported it. These are shown for review and are not scored."
                ),
                "source": "additional_metrics",
            },
        ],
        "metrics": {},
        "charts": [],
        "plots": [plot_type for _, _, plot_type in MULTIQC_PLOTS],
    }


def compute_quality(metrics: dict[str, Any]) -> str:
    """Always ``pending_review``.

    A generic report gives no basis for a pass/fail: what counts as a good
    mapping rate or duplication level is assay-specific, and guessing a
    threshold would put a confident verdict on a run nobody has calibrated.
    """
    return "pending_review"


def generate_summary(metrics: dict[str, Any]) -> str:
    """Plain-English summary of whatever was recovered."""
    samples = metrics.get("total_samples")
    depth = metrics.get("total_sequences")
    mapped = metrics.get("reads_mapped_genome")

    parts: list[str] = []
    if samples and depth:
        parts.append(f"This run covered **{samples} samples** with a mean of **{depth:,} reads per sample**.")
    elif samples:
        parts.append(f"This run covered **{samples} samples**.")
    elif depth:
        parts.append(f"This run had a mean of **{depth:,} reads per sample**.")

    if mapped is not None:
        parts.append(f"Mean genome mapping rate was **{mapped:.1%}**.")

    if not parts:
        return (
            "No standard QC metrics were found in this run's MultiQC report. The run may not have "
            "produced one, or its tools may report metrics this platform does not yet recognize."
        )

    extras = metrics.get("additional_metrics") or {}
    if extras:
        parts.append(f"{len(extras)} further tool-reported metrics are listed below for review.")
    return " ".join(parts)


def _merge(parsed: list[tuple[str, dict]]) -> dict[str, Any]:
    """Combine several MultiQC reports from one run into a single metric set.

    A run can emit one report per analysis branch. Ordering is by sample
    coverage then by URI, so the result never depends on object-listing order,
    and merging is per key: a branch that alone carries a metric still supplies
    it.
    """
    ordered = sorted(parsed, key=lambda item: (-(item[1].get("total_samples") or 0), item[0]))

    merged: dict[str, Any] = dict(EMPTY_METRICS)
    merged["metric_sources"] = {}
    merged["additional_metrics"] = {}

    for _uri, metrics in ordered:
        for key, value in metrics.items():
            if key in ("metric_sources", "additional_metrics"):
                continue
            if merged.get(key) is None and value is not None:
                merged[key] = value
                source = (metrics.get("metric_sources") or {}).get(key)
                if source:
                    merged["metric_sources"][key] = source
        for key, value in (metrics.get("additional_metrics") or {}).items():
            merged["additional_metrics"].setdefault(key, value)

    return merged


def _has_any_metric(metrics: dict[str, Any]) -> bool:
    return any(
        value is not None for key, value in metrics.items() if key not in ("metric_sources", "additional_metrics")
    ) or bool(metrics.get("additional_metrics"))


async def extract(
    session: AsyncSession | None,
    run: PipelineRun,
    *,
    skip_cache: bool = False,
    results_bucket: str | None = None,
) -> dict[str, Any]:
    """Extract QC metrics for ``run`` from whatever MultiQC report it wrote."""
    if results_bucket is None:
        results_bucket = await get_results_bucket(session)
    if not results_bucket:
        logger.warning("No results bucket configured, cannot extract metrics")
        return dict(EMPTY_METRICS)

    try:
        from app.adapters.models import StorageObjectNotFound
        from app.adapters.registry import get_storage_adapter

        adapter = get_storage_adapter()
        base = adapter.build_uri(results_bucket, "")
        prefix = f"experiments/{run.experiment_id}/pipeline-runs/{run.id}/"

        cache_uri = f"{base}{prefix}qc_metrics.json"
        if not skip_cache:
            try:
                cached = json.loads(await adapter.read_text(cache_uri))
                logger.info("Using cached qc_metrics.json for run %d", run.id)
                return cached
            except StorageObjectNotFound:
                pass

        objects = await adapter.list_objects(f"{base}{prefix}multiqc/")
        report_uris = sorted(o.storage_uri for o in objects if o.storage_uri.endswith("multiqc_data.json"))
        if not report_uris:
            logger.info("No multiqc_data.json found under multiqc/ for run %d", run.id)
            return dict(EMPTY_METRICS)

        parsed: list[tuple[str, dict]] = []
        for uri in report_uris:
            try:
                data = json.loads(await adapter.read_text(uri))
            except (ValueError, StorageObjectNotFound) as exc:
                logger.warning("Could not read MultiQC report %s for run %d: %s", uri, run.id, exc)
                continue
            parsed.append((uri, parse_multiqc_metrics(data)))

        if not parsed:
            return dict(EMPTY_METRICS)

        metrics = _merge(parsed)
        if not _has_any_metric(metrics):
            logger.info("No generic metrics found for run %d", run.id)
            return dict(EMPTY_METRICS)

        logger.info(
            "Extracted %d controlled + %d additional metrics from %d MultiQC report(s) for run %d",
            sum(1 for k, v in metrics.items() if k not in ("metric_sources", "additional_metrics") and v is not None),
            len(metrics["additional_metrics"]),
            len(parsed),
            run.id,
        )

        await adapter.write_text(cache_uri, json.dumps(metrics, indent=2), content_type="application/json")
        return metrics

    except Exception as e:
        logger.warning("Generic metric extraction failed for run %d: %s", run.id, e)
        return dict(EMPTY_METRICS)


__all__ = [
    "EMPTY_METRICS",
    "MULTIQC_PLOTS",
    "compute_quality",
    "extract",
    "generate_summary",
    "render_config",
]
