"""Produces the canonical JSON representation of a provenance report."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.services.provenance.schema import (
    ArtifactProvenanceData,
    ExperimentProvenanceData,
    PipelineRunProvenanceData,
    ProjectProvenanceData,
    ProvenanceData,
    SampleProvenanceData,
    SCHEMA_VERSION,
)


class JsonRenderer:
    @staticmethod
    def render(
        entity_type: str,
        data: ProvenanceData,
        user_email: str,
    ) -> dict[str, Any]:
        entity_section = _ENTITY_RENDERERS[entity_type](data)
        audit_trail = _get_audit_trail(data)

        return {
            "schema_version": SCHEMA_VERSION,
            "report_type": entity_type,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "generated_by": user_email,
            "bioaf_version": settings.app_version,
            "entity": entity_section,
            "audit_trail": audit_trail,
        }


def _get_audit_trail(data: ProvenanceData) -> list[dict[str, Any]]:
    if isinstance(data, ProjectProvenanceData):
        return data.audit_trail
    if isinstance(data, ExperimentProvenanceData):
        return data.audit_trail
    if isinstance(data, SampleProvenanceData):
        return data.audit_trail
    if isinstance(data, PipelineRunProvenanceData):
        return data.audit_trail
    if isinstance(data, ArtifactProvenanceData):
        return data.audit_trail
    return []


def _render_project(data: ProvenanceData) -> dict[str, Any]:
    assert isinstance(data, ProjectProvenanceData)
    return {
        "type": "project",
        **data.project,
        "experiments": data.experiments,
        "global_config": {
            "reference_datasets": data.references,
            "pipeline_defaults": {},
        },
    }


def _render_experiment(data: ProvenanceData) -> dict[str, Any]:
    assert isinstance(data, ExperimentProvenanceData)
    return {
        "type": "experiment",
        **data.experiment,
        "samples": data.samples,
        "pipeline_runs": data.pipeline_runs,
        "files": {
            "raw": data.files_raw,
            "results": data.files_results,
        },
    }


def _render_sample(data: ProvenanceData) -> dict[str, Any]:
    assert isinstance(data, SampleProvenanceData)
    return {
        "type": "sample",
        **data.sample,
        "derived_samples": data.derived_samples,
        "sample_batch": data.batch,
        "files": data.files,
        "pipeline_runs": data.pipeline_runs,
    }


def _render_pipeline_run(data: ProvenanceData) -> dict[str, Any]:
    assert isinstance(data, PipelineRunProvenanceData)
    return {
        "type": "pipeline_run",
        **data.run,
        "inputs": {
            "files": data.input_files,
            "samples": data.samples,
            "references": data.references,
        },
        "outputs": {
            "files": data.output_files,
        },
        "processes": data.processes,
    }


def _render_artifact(data: ProvenanceData) -> dict[str, Any]:
    assert isinstance(data, ArtifactProvenanceData)
    return {
        "type": "artifact",
        **data.file,
        "linked_samples": data.linked_samples,
        "downstream_usage": data.downstream_usage,
    }


_ENTITY_RENDERERS: dict[str, Any] = {
    "project": _render_project,
    "experiment": _render_experiment,
    "sample": _render_sample,
    "pipeline_run": _render_pipeline_run,
    "artifact": _render_artifact,
}
