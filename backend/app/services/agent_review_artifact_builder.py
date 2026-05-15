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

from app.models.pipeline_run import PipelineRun, PipelineRunSample
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
    header = (
        "| Sample ID | External ID | Tissue | QC status | QC notes |\n"
        "| --- | --- | --- | --- | --- |"
    )
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
    markdown = render_run_markdown(run=run, samples=samples, qc_report_content=qc_report_content)

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
