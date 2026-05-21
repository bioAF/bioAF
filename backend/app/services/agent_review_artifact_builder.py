"""Build the .md review artifact for a pipeline run (ADR-052, spec-payload).

The artifact is the ONLY user data that ever leaves the org under the LLM
review code path. Building it is deterministic; persisting it to GCS lets a
compliance reviewer fetch the exact bytes that were sent later. The never-
ship list is enforced by construction: nothing from raw rows, FASTQ blobs, or
container logs is referenced anywhere in the build path.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.experiment import Experiment
from app.models.pipeline_run import PipelineRun, PipelineRunSample
from app.models.qc_dashboard import QCDashboard
from app.models.sample import Sample

MAX_FIELD_BYTES = 50_000
TRUNCATION_NOTE_TEMPLATE = "[truncated, original size {n} bytes]"


class ArtifactBuildError(Exception):
    """Raised when the artifact cannot be assembled (e.g., missing output JSON)."""


@dataclass
class BuiltArtifact:
    markdown: str
    gcs_path: str


def _strip_html(text: str) -> str:
    """Strip HTML tags from a string and unescape entities. Cheap, dependency-free.

    Used for QC reports stored as HTML. We do not need a full parser; we only
    need to remove tags so the LLM does not have to read them.
    """
    if not text:
        return ""
    without_tags = re.sub(r"<[^>]+>", "", text)
    return html.unescape(without_tags).strip()


def _truncate(text: str) -> str:
    if text is None:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= MAX_FIELD_BYTES:
        return text
    cut = encoded[:MAX_FIELD_BYTES].decode("utf-8", errors="ignore")
    return cut + "\n" + TRUNCATION_NOTE_TEMPLATE.format(n=len(encoded))


def _format_samples_table(samples: list[Sample]) -> str:
    if not samples:
        return "_No samples linked to this run._"
    header = "| Sample ID | External ID | Tissue | QC status | QC notes |\n| --- | --- | --- | --- | --- |"
    rows = []
    for s in samples:
        ext = (s.external_id or "").replace("|", "\\|")
        tissue = (s.tissue_type or "").replace("|", "\\|")
        qc_status = s.qc_status or ""
        notes = (s.qc_notes or "").replace("\n", " ").replace("|", "\\|")
        rows.append(f"| {s.id} | {ext} | {tissue} | {qc_status} | {notes} |")
    return header + "\n" + "\n".join(rows)


def _format_parameters(parameters: dict | None) -> str:
    if not parameters:
        return "_No parameters captured._"
    lines = []
    for k, v in parameters.items():
        lines.append(f"- {k}: {v}")
    return "\n".join(lines)


def _format_output_json(output: dict | None) -> str:
    if not output:
        return "_No output JSON available._"
    return _truncate(json.dumps(output, indent=2, sort_keys=True))


def _format_qc(qc_content: str | None) -> str:
    if not qc_content:
        return "_QC report not available._"
    stripped = _strip_html(qc_content)
    return _truncate(stripped)


def _format_metrics_section(label: str, values: dict[str, Any]) -> list[str]:
    """Flatten a metrics dict into '- key: value' bullets under a header."""
    lines = [f"### {label}"]
    if not values:
        lines.append("_no metrics in this section_")
        return lines
    for k, v in values.items():
        if isinstance(v, dict):
            for sub_k, sub_v in v.items():
                lines.append(f"- {k}.{sub_k}: {sub_v}")
        else:
            lines.append(f"- {k}: {v}")
    return lines


async def _load_qc_dashboard_text(session: AsyncSession, run_id: int) -> str | None:
    """Fetch the QC dashboard for this run and serialize it as Markdown.

    Returns None when no dashboard row exists; the caller then renders the
    'QC report not available' placeholder. When a dashboard exists, returns
    a structured Markdown block with the saved summary_text plus a flattened
    dump of metrics_json (which is the same data the QC Dashboard UI renders).
    """
    result = await session.execute(select(QCDashboard).where(QCDashboard.pipeline_run_id == run_id))
    dashboard = result.scalar_one_or_none()
    if dashboard is None:
        return None

    parts: list[str] = []
    if dashboard.summary_text:
        parts.append(dashboard.summary_text.strip())
        parts.append("")
    if dashboard.status:
        parts.append(f"_Dashboard status: {dashboard.status}_")
        parts.append("")

    metrics = dashboard.metrics_json or {}
    if not metrics:
        parts.append("_QC metrics: none captured._")
        return "\n".join(parts).strip() or None

    # If metrics is grouped (dict of dicts), emit one ### section per group.
    # Otherwise treat the whole dict as a single flat section.
    grouped = all(isinstance(v, dict) for v in metrics.values()) if metrics else False
    if grouped:
        for group, values in metrics.items():
            parts.extend(_format_metrics_section(group, values))
            parts.append("")
    else:
        parts.extend(_format_metrics_section("Metrics", metrics))

    return "\n".join(parts).rstrip()


async def _load_samples_for_run(session: AsyncSession, run_id: int) -> list[Sample]:
    result = await session.execute(
        select(Sample)
        .join(PipelineRunSample, PipelineRunSample.sample_id == Sample.id)
        .where(PipelineRunSample.pipeline_run_id == run_id)
        .order_by(Sample.id)
    )
    return list(result.scalars().all())


def render_run_markdown(
    *,
    run: PipelineRun,
    samples: list[Sample],
    qc_report_content: str | None,
) -> str:
    """Pure markdown rendering, no DB access. Testable in isolation."""
    if not run.output_files_json and not run.parameters_json:
        # Allow empty parameters as long as some output exists; if both are
        # missing we have no signal worth shipping.
        pass
    sections: list[str] = []
    sections.append("# Pipeline Run Review Input")
    sections.append("")
    sections.append("## Run")
    sections.append(f"- ID: {run.id}")
    sections.append(f"- Name: {run.pipeline_name}")
    sections.append(f"- Status: {run.status}")
    sections.append(f"- Started: {run.started_at}")
    sections.append(f"- Completed: {run.completed_at}")
    pipeline_version = run.pipeline_version or "unknown"
    sections.append(f"- Pipeline: {run.pipeline_name} v{pipeline_version}")
    sections.append("")
    sections.append("## Parameters")
    sections.append(_format_parameters(run.parameters_json))
    sections.append("")
    sections.append("## Samples")
    sections.append(_format_samples_table(samples))
    sections.append("")
    sections.append("## Output JSON")
    sections.append("```json")
    sections.append(_format_output_json(run.output_files_json))
    sections.append("```")
    sections.append("")
    sections.append("## QC Report")
    sections.append(_format_qc(qc_report_content))
    sections.append("")
    sections.append("## Errors")
    sections.append(_truncate(run.error_message) if run.error_message else "_none_")
    sections.append("")
    return "\n".join(sections)


async def build_for_run(
    session: AsyncSession,
    run_id: int,
    *,
    qc_report_content: str | None = None,
    gcs_writer: Any | None = None,
    job_id: int | None = None,
    expand_literature_to_project: bool = False,
) -> BuiltArtifact:
    """Build the .md artifact for a single pipeline run.

    gcs_writer is an optional callable `(gcs_path, content) -> None` so tests
    can substitute a recorder; in production this is the GCS upload path.
    """
    result = await session.execute(select(PipelineRun).where(PipelineRun.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise ArtifactBuildError(f"pipeline run {run_id} not found")
    if not run.output_files_json:
        raise ArtifactBuildError(f"pipeline run {run_id} has no output JSON; cannot build artifact")

    samples = await _load_samples_for_run(session, run_id)
    # Auto-load the QC dashboard contents when the caller did not pass an
    # explicit override. Without this fallback the artifact would render
    # "QC report not available" even when a QC dashboard is sitting in the
    # database, which is what the LLM correctly flagged on the first run.
    resolved_qc = qc_report_content
    if resolved_qc is None:
        resolved_qc = await _load_qc_dashboard_text(session, run_id)
    markdown = render_run_markdown(run=run, samples=samples, qc_report_content=resolved_qc)

    # ADR-057: append the Literature section when configured. The pipeline run's
    # owning experiment is the scope; toggles per org/project/experiment override.
    if run.experiment_id is not None:
        from app.services.literature.agent_review_payload import build_literature_payload

        payload = await build_literature_payload(
            session,
            org_id=run.organization_id,
            scope_type="experiment",
            scope_id=run.experiment_id,
            expand_to_project=expand_literature_to_project,
        )
        if payload.markdown:
            markdown = markdown + "\n" + payload.markdown

    suffix = f"_job{job_id}" if job_id is not None else ""
    gcs_path = f"gs://bioaf-agent-reviews/pipeline_runs/{run_id}/agent_review_inputs/agent_review_input{suffix}.md"
    if gcs_writer is not None:
        await gcs_writer(gcs_path, markdown)

    return BuiltArtifact(markdown=markdown, gcs_path=gcs_path)


def render_experiment_header(
    *,
    experiment_id: int,
    experiment_name: str,
    experiment_status: str,
    included_run_ids: list[int],
) -> str:
    sections = [
        "# Experiment Run Comparison Input",
        "",
        "## Experiment",
        f"- ID: {experiment_id}",
        f"- Name: {experiment_name}",
        f"- Status: {experiment_status}",
        "",
        "## Included Runs",
    ]
    for rid in included_run_ids:
        sections.append(f"- {rid}")
    sections.append("")
    return "\n".join(sections)


def _format_experiment_metadata(exp: Experiment) -> list[str]:
    """Surface every experiment field that helps the LLM judge cross-run design.

    Sparse fields (None / empty) are omitted so the header stays scannable.
    """
    lines: list[str] = ["## Experiment", f"- ID: {exp.id}", f"- Name: {exp.name}"]
    optional_fields: list[tuple[str, Any]] = [
        ("Code", exp.code),
        ("External ID", exp.external_id),
        ("Status", exp.status),
        ("Design type", exp.design_type),
        ("Protocol version", exp.protocol_version),
        ("Start date", exp.start_date.isoformat() if exp.start_date else None),
        ("Expected sample count", exp.expected_sample_count),
    ]
    for label, value in optional_fields:
        if value is None or value == "":
            continue
        lines.append(f"- {label}: {value}")
    if exp.hypothesis:
        lines.append("")
        lines.append("### Hypothesis")
        lines.append(_truncate(exp.hypothesis))
    if exp.description:
        lines.append("")
        lines.append("### Description")
        lines.append(_truncate(exp.description))
    if exp.variables_json:
        lines.append("")
        lines.append("### Design variables")
        lines.append("```json")
        lines.append(_truncate(json.dumps(exp.variables_json, indent=2, sort_keys=True)))
        lines.append("```")
    return lines


def _format_experiment_samples_table(samples: list[Sample]) -> list[str]:
    """Wide samples table for the experiment-scope artifact.

    Includes per-sample design + QC fields that a per-run review never has the
    context to surface (cross-donor variability, treatment-arm imbalance, etc.).
    """
    lines = ["## Samples in this experiment"]
    if not samples:
        lines.append("_No samples on this experiment._")
        return lines
    header = (
        "| Sample ID | External ID | Organism | Tissue | Donor | Treatment | "
        "Viability % | Cell count | QC status | Status | Notes |"
    )
    divider = "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"
    rows = [header, divider]
    for s in samples:
        cells = [
            str(s.id),
            (s.external_id or "").replace("|", "\\|"),
            (s.organism or "").replace("|", "\\|"),
            (s.tissue_type or "").replace("|", "\\|"),
            (s.donor_source or "").replace("|", "\\|"),
            (s.treatment_condition or "").replace("|", "\\|"),
            (str(s.viability_pct) if s.viability_pct is not None else ""),
            (str(s.cell_count) if s.cell_count is not None else ""),
            (s.qc_status or ""),
            (s.status or ""),
            (s.qc_notes or "").replace("\n", " ").replace("|", "\\|"),
        ]
        rows.append("| " + " | ".join(cells) + " |")
    lines.extend(rows)
    return lines


async def build_experiment_header(
    session: AsyncSession,
    *,
    experiment_id: int,
    included_run_ids: list[int],
    expand_literature_to_project: bool = False,
) -> str:
    """Build the experiment-scope header for a Button B review.

    Loads the experiment row plus every sample scoped to that experiment so
    the LLM can reason about design intent and per-sample context across all
    runs, not just the subset of samples that happen to be linked to the
    included runs. Per-run artifacts still follow this header.
    """
    exp_result = await session.execute(select(Experiment).where(Experiment.id == experiment_id))
    exp = exp_result.scalar_one_or_none()
    if exp is None:
        raise ArtifactBuildError(f"experiment {experiment_id} not found")

    samples_result = await session.execute(
        select(Sample).where(Sample.experiment_id == experiment_id).order_by(Sample.id)
    )
    samples = list(samples_result.scalars().all())

    sections: list[str] = ["# Experiment Run Comparison Input", ""]
    sections.extend(_format_experiment_metadata(exp))
    sections.append("")
    sections.append("## Included Runs")
    if included_run_ids:
        for rid in included_run_ids:
            sections.append(f"- {rid}")
    else:
        sections.append("_No pipeline runs selected._")
    sections.append("")
    sections.extend(_format_experiment_samples_table(samples))
    sections.append("")

    # ADR-057: append the Literature section when configured.
    from app.services.literature.agent_review_payload import build_literature_payload

    payload = await build_literature_payload(
        session,
        org_id=exp.organization_id,
        scope_type="experiment",
        scope_id=experiment_id,
        expand_to_project=expand_literature_to_project,
    )
    if payload.markdown:
        sections.append(payload.markdown)

    return "\n".join(sections)
