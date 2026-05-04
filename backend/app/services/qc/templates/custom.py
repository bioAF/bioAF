"""Custom-pipeline QC template.

The pipeline is responsible for emitting both the metrics blob
(`qc_metrics.json` in the run output prefix) and the render config (stored
on the pipeline version as `qc_config_json`). This template just plumbs them
through.
"""

from __future__ import annotations

from typing import Any


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


__all__ = ["render_config", "compute_quality"]
