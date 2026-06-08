"""Single source of truth for Nextflow trace.tsv parsing (BAL rework, Phase 5).

A Nextflow ``trace.tsv`` describes the tasks of a Nextflow run: one row per
task attempt, columns like ``status``, ``%cpu``, ``peak_rss``, ``realtime``.
Parsing it is Nextflow-domain logic, not Kubernetes-domain logic, so it lives
here in the leaf ``app.pipeline`` layer where both the compute adapter and the
pipeline-monitor service can import it (SLURM, which also runs Nextflow, reuses
it too).

Two consumers, two shapes, one parser:
  - ``parse_trace_rows`` returns the raw header-keyed rows; the monitor builds
    ``PipelineProcess`` records from them with the normalizers below.
  - ``parse_trace_to_progress`` returns the normalized + aggregated progress
    dict the compute adapter's ``get_job_progress`` surfaces.

Normalizers are the converged *superset* (owner decision): they handle
``GB``/``MB``/``KB``/bare-float memory and the full duration grammar, so both
call sites agree.
"""

from __future__ import annotations

import csv
import io

# Nextflow task status -> normalized BAL status.
_NF_STATUS_MAP = {
    "COMPLETED": "completed",
    "RUNNING": "running",
    "FAILED": "failed",
    "CACHED": "cached",
    "SUBMITTED": "pending",
    "PENDING": "pending",
    "ABORTED": "failed",
}


def parse_trace_rows(content: str) -> list[dict]:
    """Parse trace.tsv text into a list of raw, header-keyed row dicts."""
    reader = csv.DictReader(io.StringIO(content), delimiter="\t")
    return [dict(row) for row in reader]


def map_nf_status(nf_status: str) -> str:
    """Map a Nextflow status string to the normalized BAL status."""
    return _NF_STATUS_MAP.get(nf_status.upper(), nf_status.lower())


def safe_int(val) -> int | None:
    if val is None or val == "" or val == "-":
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def safe_float(val) -> float | None:
    if val is None or val == "" or val == "-":
        return None
    try:
        return float(str(val).replace("%", ""))
    except (ValueError, TypeError):
        return None


def parse_memory_gb(val) -> float | None:
    """Parse memory values like '1.2 GB', '500 MB', '2048 KB', or a bare float (GB)."""
    if not val or val == "-":
        return None
    try:
        val = str(val).strip()
        if "GB" in val.upper():
            return float(val.upper().replace("GB", "").strip())
        if "MB" in val.upper():
            return round(float(val.upper().replace("MB", "").strip()) / 1024, 2)
        if "KB" in val.upper():
            return round(float(val.upper().replace("KB", "").strip()) / (1024 * 1024), 4)
        return float(val)
    except (ValueError, TypeError):
        return None


def parse_duration_s(val) -> int | None:
    """Parse durations like '5m 30s', '1h 2m 3s', '250ms' into whole seconds."""
    if not val or val == "-":
        return None
    try:
        val = str(val).strip()
        # Nextflow trace emits milliseconds for sub-second tasks.
        if val.endswith("ms"):
            return int(float(val[:-2]) / 1000)
        if val.endswith("s") and "m" not in val and "h" not in val:
            return int(float(val[:-1]))
        seconds = 0
        if "h" in val:
            parts = val.split("h")
            seconds += int(parts[0].strip()) * 3600
            val = parts[1].strip()
        if "m" in val:
            parts = val.split("m")
            seconds += int(parts[0].strip()) * 60
            val = parts[1].strip()
        if val.endswith("s"):
            seconds += int(float(val[:-1]))
        return seconds if seconds > 0 else None
    except (ValueError, TypeError):
        return None


def parse_trace_to_progress(content: str) -> dict:
    """Parse trace.tsv into the normalized ``{percent_complete, processes}`` dict.

    Each process carries normalized first-class fields with zero defaults for
    unparseable numeric columns (matching the compute adapter's contract):
    ``task_id, attempt, name, status, cpu, memory_gb, duration_s``.
    """
    rows = parse_trace_rows(content)
    if not rows:
        return {"percent_complete": 0.0, "processes": []}

    processes = []
    completed_count = 0
    for row in rows:
        status = map_nf_status(row.get("status", ""))
        if status in ("completed", "cached"):
            completed_count += 1

        attempt = safe_int(row.get("attempt", "1"))
        processes.append(
            {
                "task_id": row.get("task_id", "") or "",
                "attempt": attempt if attempt is not None else 1,
                "name": row.get("name", "") or row.get("process", ""),
                "status": status,
                "cpu": safe_float(row.get("%cpu")) or 0.0,
                "memory_gb": parse_memory_gb(row.get("peak_rss")) or 0.0,
                "duration_s": parse_duration_s(row.get("realtime")) or 0,
            }
        )

    total = len(processes)
    pct = round(completed_count / total * 100, 1) if total > 0 else 0.0
    return {"percent_complete": pct, "processes": processes}
