"""Custom-pipeline QC template.

The pipeline is responsible for emitting both the metrics blob
(`qc_metrics.json` in the run output prefix) and the render config (stored
on the pipeline version as `qc_config_json`). This template just plumbs them
through.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline_run import PipelineRun
from app.services.qc.extractors.gcs_helpers import get_results_bucket

logger = logging.getLogger("bioaf.qc.custom")


def render_config(override: dict | None = None) -> dict:
    if override:
        merged = dict(override)
        merged.setdefault("template", "custom")
        merged.setdefault("sections", [])
        merged.setdefault("metrics", {})
        merged.setdefault("charts", [])
        merged.setdefault("plots", [])
        return merged

    return {
        "template": "custom",
        "sections": [],
        "metrics": {},
        "charts": [],
        "plots": [],
    }


def compute_quality(metrics: dict[str, Any]) -> str:
    rating = metrics.get("quality_rating")
    if isinstance(rating, str) and rating:
        return rating
    return "pending_review"


async def extract(
    session: AsyncSession,
    run: PipelineRun,
    *,
    skip_cache: bool = False,
    results_bucket: str | None = None,
) -> dict[str, Any]:
    """Custom pipelines emit qc_metrics.json directly into the run output prefix;
    this reads it back as-is (no parsing -- the pipeline owns the metric shape)."""
    if results_bucket is None:
        results_bucket = await get_results_bucket(session)
    if not results_bucket:
        logger.warning("No results bucket configured, cannot extract metrics")
        return {}

    try:
        from app.adapters.models import StorageObjectNotFound
        from app.adapters.registry import get_storage_adapter

        adapter = get_storage_adapter()
        prefix = f"experiments/{run.experiment_id}/pipeline-runs/{run.id}/"
        uri = adapter.build_uri(results_bucket, f"{prefix}qc_metrics.json")
        try:
            return json.loads(await adapter.read_text(uri))
        except StorageObjectNotFound:
            logger.info("No qc_metrics.json found for custom pipeline run %d", run.id)
            return {}
    except Exception as e:
        logger.warning("Custom-pipeline metric extraction failed for run %d: %s", run.id, e)
        return {}


def generate_summary(metrics: dict[str, Any]) -> str:
    emitted = metrics.get("summary_text")
    if isinstance(emitted, str) and emitted:
        return emitted
    quality = metrics.get("quality_rating", "pending_review")
    return f"Custom pipeline run. Overall quality: **{quality.capitalize()}**."


__all__ = ["render_config", "compute_quality", "extract", "generate_summary"]
