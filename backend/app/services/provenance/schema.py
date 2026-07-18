"""Report data structures and schema version constant."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "1.0"

# Entity type literals
ENTITY_TYPES = ("project", "experiment", "sample", "pipeline_run", "artifact", "validation_study")


@dataclass
class ReportOutput:
    """Container for a rendered report."""

    content: bytes | str
    content_type: str
    filename: str


@dataclass
class UserRef:
    """Minimal user reference for display."""

    id: int
    email: str
    name: str | None = None


@dataclass
class ProjectProvenanceData:
    project: dict[str, Any] = field(default_factory=dict)
    experiments: list[dict[str, Any]] = field(default_factory=list)
    samples: list[dict[str, Any]] = field(default_factory=list)
    pipeline_runs: list[dict[str, Any]] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)
    references: list[dict[str, Any]] = field(default_factory=list)
    snapshots: list[dict[str, Any]] = field(default_factory=list)
    audit_trail: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ExperimentProvenanceData:
    experiment: dict[str, Any] = field(default_factory=dict)
    samples: list[dict[str, Any]] = field(default_factory=list)
    batches: list[dict[str, Any]] = field(default_factory=list)
    pipeline_runs: list[dict[str, Any]] = field(default_factory=list)
    files_raw: list[dict[str, Any]] = field(default_factory=list)
    files_results: list[dict[str, Any]] = field(default_factory=list)
    references: list[dict[str, Any]] = field(default_factory=list)
    custom_fields: list[dict[str, Any]] = field(default_factory=list)
    audit_trail: list[dict[str, Any]] = field(default_factory=list)
    # lit_validation A3 reverse link: set when this experiment is the reproduction target of a
    # ValidationStudy, so the experiment's own report can cite the source paper it reproduces.
    validation: dict[str, Any] | None = None


@dataclass
class SampleProvenanceData:
    sample: dict[str, Any] = field(default_factory=dict)
    parent_sample: dict[str, Any] | None = None
    derived_samples: list[dict[str, Any]] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)
    pipeline_runs: list[dict[str, Any]] = field(default_factory=list)
    batch: dict[str, Any] | None = None
    audit_trail: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PipelineRunProvenanceData:
    run: dict[str, Any] = field(default_factory=dict)
    processes: list[dict[str, Any]] = field(default_factory=list)
    input_files: list[dict[str, Any]] = field(default_factory=list)
    output_files: list[dict[str, Any]] = field(default_factory=list)
    samples: list[dict[str, Any]] = field(default_factory=list)
    references: list[dict[str, Any]] = field(default_factory=list)
    resume_from: dict[str, Any] | None = None
    audit_trail: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ArtifactProvenanceData:
    file: dict[str, Any] = field(default_factory=dict)
    source_pipeline_run: dict[str, Any] | None = None
    linked_samples: list[dict[str, Any]] = field(default_factory=list)
    downstream_usage: list[dict[str, Any]] = field(default_factory=list)
    audit_trail: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ValidationStudyProvenanceData:
    """A lit_validation reproduction attempt (A3 + F3). Wires the source paper to the reproduction
    plan, the created experiment, and the two pipeline runs (data acquisition + analysis), alongside
    the computed-vs-claimed evidence bundle and the classifier's verdict."""

    study: dict[str, Any] = field(default_factory=dict)
    source_paper: dict[str, Any] | None = None
    reproduction_plan: dict[str, Any] | None = None
    comparison_targets: list[dict[str, Any]] = field(default_factory=list)
    experiment: dict[str, Any] | None = None
    pipeline_runs: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, Any] | None = None
    audit_trail: list[dict[str, Any]] = field(default_factory=list)


ProvenanceData = (
    ProjectProvenanceData
    | ExperimentProvenanceData
    | SampleProvenanceData
    | PipelineRunProvenanceData
    | ArtifactProvenanceData
    | ValidationStudyProvenanceData
)
