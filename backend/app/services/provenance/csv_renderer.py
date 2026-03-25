"""Produces tabular CSV files from a JSON provenance report."""

from __future__ import annotations

import csv
import io
from typing import Any


def _csv_str(headers: list[str], rows: list[list[Any]]) -> str:
    """Build a CSV string from headers and rows."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(["" if v is None else v for v in row])
    return buf.getvalue()


class CsvRenderer:
    @staticmethod
    def render(entity_type: str, json_report: dict[str, Any]) -> dict[str, str]:
        """Return a dict of {filename: csv_content_string}."""
        renderer = _CSV_RENDERERS.get(entity_type)
        if not renderer:
            raise ValueError(f"Unknown entity type: {entity_type}")
        return renderer(json_report)


def _render_experiment_csv(report: dict[str, Any]) -> dict[str, str]:
    entity = report["entity"]
    result: dict[str, str] = {}

    # sample_manifest.csv
    samples = entity.get("samples", [])
    result["sample_manifest.csv"] = _csv_str(
        [
            "Sample ID",
            "External ID",
            "Organism",
            "Tissue Type",
            "QC Status",
            "Library Prep",
            "Library Layout",
            "Molecule Type",
            "Chemistry Version",
            "Donor Source",
            "Treatment",
            "Status",
        ],
        [
            [
                s.get("id"),
                s.get("external_id"),
                s.get("biological", {}).get("organism"),
                s.get("biological", {}).get("tissue_type"),
                s.get("qc", {}).get("status"),
                s.get("technical", {}).get("library_prep_method"),
                s.get("technical", {}).get("library_layout"),
                s.get("technical", {}).get("molecule_type"),
                s.get("technical", {}).get("chemistry_version"),
                s.get("biological", {}).get("donor_source"),
                s.get("biological", {}).get("treatment_condition"),
                s.get("status"),
            ]
            for s in samples
        ],
    )

    # file_manifest.csv
    files = entity.get("files", {})
    all_files = files.get("raw", []) + files.get("results", [])
    result["file_manifest.csv"] = _csv_str(
        ["File ID", "Filename", "Type", "Size (bytes)", "MD5", "SHA-256", "Source Type", "Artifact Type", "GCS URI"],
        [
            [
                f.get("id"),
                f.get("filename"),
                f.get("file_type"),
                f.get("size_bytes"),
                f.get("md5"),
                f.get("sha256"),
                f.get("source_type", "upload"),
                f.get("artifact_type"),
                f.get("gcs_uri"),
            ]
            for f in all_files
        ],
    )

    # pipeline_runs.csv
    runs = entity.get("pipeline_runs", [])
    result["pipeline_runs.csv"] = _csv_str(
        [
            "Run ID",
            "Pipeline",
            "Version",
            "Status",
            "Submitted By",
            "Started At",
            "Completed At",
            "Reference Genome",
            "Alignment",
        ],
        [
            [
                r.get("id"),
                r.get("pipeline_name"),
                r.get("pipeline_version"),
                r.get("status"),
                (r.get("submitted_by") or {}).get("email"),
                r.get("started_at"),
                r.get("completed_at"),
                r.get("reference_genome"),
                r.get("alignment_algorithm"),
            ]
            for r in runs
        ],
    )

    # process_steps.csv
    process_rows: list[list[Any]] = []
    for run in runs:
        for p in run.get("processes", []):
            process_rows.append(
                [
                    run.get("id"),
                    run.get("pipeline_name"),
                    p.get("name"),
                    p.get("status"),
                    p.get("exit_code"),
                    p.get("cpu_usage"),
                    p.get("memory_peak_gb"),
                    p.get("duration_seconds"),
                ]
            )
    result["process_steps.csv"] = _csv_str(
        ["Run ID", "Pipeline", "Step", "Status", "Exit Code", "CPU", "Memory (GB)", "Duration (s)"],
        process_rows,
    )

    return result


def _render_project_csv(report: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}

    result["sample_manifest.csv"] = _csv_str(
        ["Sample ID", "External ID", "Organism", "Tissue Type", "QC Status", "Experiment ID"],
        [],  # project-level samples don't have full detail in entity
    )

    result["file_manifest.csv"] = _csv_str(
        ["File ID", "Filename", "Type", "Size (bytes)", "MD5", "SHA-256", "GCS URI"],
        [],
    )

    result["pipeline_runs.csv"] = _csv_str(
        ["Run ID", "Pipeline", "Version", "Status", "Submitted By", "Started At", "Completed At"],
        [],
    )

    result["process_steps.csv"] = _csv_str(
        ["Run ID", "Pipeline", "Step", "Status", "Exit Code"],
        [],
    )

    return result


def _render_sample_csv(report: dict[str, Any]) -> dict[str, str]:
    entity = report["entity"]
    files = entity.get("files", [])
    runs = entity.get("pipeline_runs", [])

    return {
        "files.csv": _csv_str(
            ["File ID", "Filename", "Type", "Size (bytes)", "MD5", "SHA-256"],
            [
                [f.get("id"), f.get("filename"), f.get("file_type"), f.get("size_bytes"), f.get("md5"), f.get("sha256")]
                for f in files
            ],
        ),
        "pipeline_runs.csv": _csv_str(
            ["Run ID", "Pipeline", "Version", "Status", "Submitted By"],
            [
                [
                    r.get("id"),
                    r.get("pipeline_name"),
                    r.get("pipeline_version"),
                    r.get("status"),
                    (r.get("submitted_by") or {}).get("email"),
                ]
                for r in runs
            ],
        ),
    }


def _render_pipeline_run_csv(report: dict[str, Any]) -> dict[str, str]:
    entity = report["entity"]
    processes = entity.get("processes", [])
    inputs = entity.get("inputs", {})
    outputs = entity.get("outputs", {})

    return {
        "process_steps.csv": _csv_str(
            ["Step", "Task ID", "Status", "Exit Code", "CPU", "Memory (GB)", "Duration (s)"],
            [
                [
                    p.get("name"),
                    p.get("task_id"),
                    p.get("status"),
                    p.get("exit_code"),
                    p.get("cpu_usage"),
                    p.get("memory_peak_gb"),
                    p.get("duration_seconds"),
                ]
                for p in processes
            ],
        ),
        "input_files.csv": _csv_str(
            ["File ID", "Filename", "Type", "Size (bytes)", "MD5", "SHA-256"],
            [
                [f.get("id"), f.get("filename"), f.get("file_type"), f.get("size_bytes"), f.get("md5"), f.get("sha256")]
                for f in inputs.get("files", [])
            ],
        ),
        "output_files.csv": _csv_str(
            ["File ID", "Filename", "Type", "Size (bytes)", "MD5", "SHA-256", "Artifact Type"],
            [
                [
                    f.get("id"),
                    f.get("filename"),
                    f.get("file_type"),
                    f.get("size_bytes"),
                    f.get("md5"),
                    f.get("sha256"),
                    f.get("artifact_type"),
                ]
                for f in outputs.get("files", [])
            ],
        ),
    }


def _render_artifact_csv(report: dict[str, Any]) -> dict[str, str]:
    entity = report["entity"]
    linked = entity.get("linked_samples", [])
    downstream = entity.get("downstream_usage", [])

    rows: list[list[Any]] = []
    for s in linked:
        rows.append(["linked_sample", s.get("id"), s.get("external_id"), s.get("organism"), ""])
    for d in downstream:
        rows.append(["downstream_run", d.get("pipeline_run_id"), "", "", d.get("pipeline_name")])

    return {
        "lineage.csv": _csv_str(
            ["Relationship", "Entity ID", "External ID", "Organism", "Pipeline"],
            rows,
        ),
    }


_CSV_RENDERERS: dict[str, Any] = {
    "project": _render_project_csv,
    "experiment": _render_experiment_csv,
    "sample": _render_sample_csv,
    "pipeline_run": _render_pipeline_run_csv,
    "artifact": _render_artifact_csv,
}
