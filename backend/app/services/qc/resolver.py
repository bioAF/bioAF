"""Resolve which QC template + render config a pipeline run should use.

Given a PipelineRun, the resolver returns (template_name, render_config) by
walking the chain: custom_pipeline_version override -> pipeline_catalog
override -> built-in template default. Used by QCDashboardService when
generating a dashboard, and again on read to fill in old dashboards that
predate the qc_config_json snapshot.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.custom_pipeline_version import CustomPipelineVersion
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.models.pipeline_run import PipelineRun
from app.services.qc.templates import TEMPLATES, custom as custom_template, get_template


def deep_merge(base: dict, override: dict) -> dict:
    """Recursive dict merge. Override wins on scalars; lists are replaced
    wholesale (we don't try to merge sections by id)."""
    out: dict = {}
    for k, v in base.items():
        out[k] = v
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


async def resolve_template_for_run(session: AsyncSession, run: PipelineRun) -> tuple[str, dict[str, Any]]:
    template_name, override = await _resolve_template_and_override(session, run)
    return template_name, _build_config(template_name, override)


async def _resolve_template_and_override(session: AsyncSession, run: PipelineRun) -> tuple[str, dict | None]:
    if run.custom_pipeline_version_id is not None:
        version = await session.get(CustomPipelineVersion, run.custom_pipeline_version_id)
        if version is not None:
            return (version.qc_template or "custom", version.qc_config_json)

    if run.pipeline_name:
        result = await session.execute(
            select(PipelineCatalogEntry).where(
                PipelineCatalogEntry.organization_id == run.organization_id,
                PipelineCatalogEntry.pipeline_key == run.pipeline_name,
            )
        )
        entry = result.scalar_one_or_none()
        if entry is not None:
            return (entry.qc_template or "scrnaseq", entry.qc_config_json)

    return ("scrnaseq", None)


def _build_config(template_name: str, override: dict | None) -> dict[str, Any]:
    template = get_template(template_name)
    if template_name == "custom":
        return custom_template.render_config(override=override)

    base = template.render_config()
    if override:
        return deep_merge(base, override)
    return base


def build_config_for_template(template_name: str, override: dict | None = None) -> dict[str, Any]:
    """Sync helper used at dashboard read time when the dashboard's
    qc_config_json is NULL (pre-migration row) -- substitute the template's
    default config so the page still renders."""
    return _build_config(template_name, override)


__all__ = [
    "deep_merge",
    "resolve_template_for_run",
    "build_config_for_template",
    "TEMPLATES",
]
