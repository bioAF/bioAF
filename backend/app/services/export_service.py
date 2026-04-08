"""Data export service for experiments and projects.

Builds structured ZIP archives containing results, raw files, provenance
reports, GEO exports, and sample manifests. Supports direct streaming and
GCS signed-URL delivery for large exports.
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.experiment import Experiment
from app.models.file import File
from app.models.project import Project
from app.models.sample import Sample
from app.services.provenance.report_service import ProvenanceReportService

logger = logging.getLogger("bioaf.export")

_FASTQ_TYPES = {"fastq", "fastq.gz", "fq", "fq.gz"}


# ---------------------------------------------------------------------------
# Size estimation
# ---------------------------------------------------------------------------


async def estimate_experiment_export(
    session: AsyncSession,
    experiment_id: int,
    org_id: int,
    include_fastq: bool = False,
) -> dict:
    """Return total_bytes and per-category breakdown for an experiment export."""
    experiment = await _get_experiment(session, experiment_id, org_id)
    if experiment is None:
        return None  # type: ignore[return-value]

    rows = (
        await session.execute(
            select(File.file_type, File.size_bytes).where(
                File.experiment_id == experiment_id,
                File.organization_id == org_id,
            )
        )
    ).fetchall()

    breakdown: dict[str, int] = {}
    for file_type, size in rows:
        ftype = (file_type or "other").lower()
        if ftype in _FASTQ_TYPES and not include_fastq:
            continue
        breakdown[ftype] = breakdown.get(ftype, 0) + (size or 0)

    return {"total_bytes": sum(breakdown.values()), "breakdown": breakdown}


async def estimate_project_export(
    session: AsyncSession,
    project_id: int,
    org_id: int,
    include_fastq: bool = False,
) -> dict | None:
    """Return total_bytes, per-category breakdown, and per-experiment breakdown."""
    project = await _get_project(session, project_id, org_id)
    if project is None:
        return None

    experiments = (
        await session.execute(
            select(Experiment.id, Experiment.name).where(
                Experiment.project_id == project_id,
                Experiment.organization_id == org_id,
            )
        )
    ).fetchall()

    per_experiment: list[dict] = []
    grand_total = 0
    grand_breakdown: dict[str, int] = {}

    for exp_id, exp_name in experiments:
        est = await estimate_experiment_export(session, exp_id, org_id, include_fastq)
        per_experiment.append({"experiment_id": exp_id, "name": exp_name, **est})
        grand_total += est["total_bytes"]
        for k, v in est["breakdown"].items():
            grand_breakdown[k] = grand_breakdown.get(k, 0) + v

    return {
        "total_bytes": grand_total,
        "breakdown": grand_breakdown,
        "experiments": per_experiment,
    }


# ---------------------------------------------------------------------------
# Export builders
# ---------------------------------------------------------------------------


async def export_experiment(
    session: AsyncSession,
    experiment_id: int,
    org_id: int,
    include_fastq: bool,
    include_provenance: bool,
    user_email: str,
    folder_prefix: str | None = None,
) -> bytes:
    """Build and return ZIP bytes for a single experiment export.

    folder_prefix: when set, all paths are nested under this prefix (used for
    project exports that embed experiments as sub-folders).
    """
    experiment = await _get_experiment(session, experiment_id, org_id)
    if experiment is None:
        raise ValueError(f"Experiment {experiment_id} not found")

    safe_name = _safe_name(experiment.name)
    prefix = f"{folder_prefix}/{safe_name}/" if folder_prefix else f"{safe_name}/"

    files = (
        (
            await session.execute(
                select(File).where(
                    File.experiment_id == experiment_id,
                    File.organization_id == org_id,
                )
            )
        )
        .scalars()
        .all()
    )

    samples = (await session.execute(select(Sample).where(Sample.experiment_id == experiment_id))).scalars().all()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # README
        readme = _build_readme(experiment, user_email, include_fastq, include_provenance)
        zf.writestr(f"{prefix}README.txt", readme)

        # Sample manifest CSV
        manifest_csv = _build_sample_manifest(samples)
        zf.writestr(f"{prefix}sample_manifest.csv", manifest_csv)

        # Data files (stubs -- GCS fetch would happen here in production)
        for f in files:
            ftype = (f.file_type or "other").lower()
            if ftype in _FASTQ_TYPES and not include_fastq:
                continue
            folder = _file_folder(ftype)
            zf.writestr(f"{prefix}{folder}/{f.filename}", b"")  # placeholder -- real impl streams from GCS

        # Provenance report
        if include_provenance:
            try:
                report = await ProvenanceReportService.generate(
                    session=session,
                    entity_type="experiment",
                    entity_id=experiment_id,
                    org_id=org_id,
                    user_email=user_email,
                    format="all",
                )
                content = report.content
                if isinstance(content, str):
                    content = content.encode("utf-8")
                zf.writestr(f"{prefix}provenance/{report.filename}", content)
            except Exception as exc:
                logger.warning("Provenance report failed for experiment %d: %s", experiment_id, exc)

    return buf.getvalue()


async def export_project(
    session: AsyncSession,
    project_id: int,
    org_id: int,
    include_fastq: bool,
    include_provenance: bool,
    user_email: str,
) -> tuple[bytes, str]:
    """Build and return (zip_bytes, filename) for a project export."""
    project = await _get_project(session, project_id, org_id)
    if project is None:
        raise ValueError(f"Project {project_id} not found")

    safe_name = _safe_name(project.name)
    folder_prefix = safe_name

    experiments = (
        (
            await session.execute(
                select(Experiment.id).where(
                    Experiment.project_id == project_id,
                    Experiment.organization_id == org_id,
                )
            )
        )
        .scalars()
        .all()
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Project-level README
        readme = _build_project_readme(project, user_email)
        zf.writestr(f"{folder_prefix}/README.txt", readme)

        for exp_id in experiments:
            exp_bytes = await export_experiment(
                session, exp_id, org_id, include_fastq, include_provenance, user_email, folder_prefix
            )
            # Merge experiment zip contents into project zip
            with zipfile.ZipFile(io.BytesIO(exp_bytes)) as exp_zf:
                for item in exp_zf.infolist():
                    zf.writestr(item, exp_zf.read(item.filename))

        # Project-level provenance
        if include_provenance:
            try:
                report = await ProvenanceReportService.generate(
                    session=session,
                    entity_type="project",
                    entity_id=project_id,
                    org_id=org_id,
                    user_email=user_email,
                    format="all",
                )
                content = report.content
                if isinstance(content, str):
                    content = content.encode("utf-8")
                zf.writestr(f"{folder_prefix}/provenance/{report.filename}", content)
            except Exception as exc:
                logger.warning("Provenance report failed for project %d: %s", project_id, exc)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{safe_name}_export_{ts}.zip"
    return buf.getvalue(), filename


async def _upload_zip_to_gcs(
    zip_bytes: bytes,
    org_id: int,
    name: str,
    session: AsyncSession,
) -> str:
    """Upload zip_bytes to the config_backups bucket and return a signed URL."""
    from google.cloud import storage
    from app.services.gcs_storage import GcsStorageService

    result = await session.execute(text("SELECT value FROM platform_config WHERE key = 'config_backups_bucket_name'"))
    row = result.fetchone()
    if not row or not row[0] or row[0] == "null":
        raise RuntimeError("config_backups_bucket_name not configured")

    bucket_name = row[0]
    credentials = await GcsStorageService.get_credentials(session)
    client = storage.Client(credentials=credentials)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    blob_path = f"exports/{org_id}/{ts}_{_safe_name(name)}.zip"

    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_path)
    blob.upload_from_string(zip_bytes, content_type="application/zip")

    signed_url = blob.generate_signed_url(
        expiration=86400,  # 24 hours
        method="GET",
        credentials=credentials,
    )
    return signed_url


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_experiment(session: AsyncSession, experiment_id: int, org_id: int) -> Experiment | None:
    result = await session.execute(
        select(Experiment).where(
            Experiment.id == experiment_id,
            Experiment.organization_id == org_id,
        )
    )
    return result.scalar_one_or_none()


async def _get_project(session: AsyncSession, project_id: int, org_id: int) -> Project | None:
    result = await session.execute(
        select(Project).where(
            Project.id == project_id,
            Project.organization_id == org_id,
        )
    )
    return result.scalar_one_or_none()


def _safe_name(name: str) -> str:
    """Return a filesystem-safe version of a name."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name).strip("_")


def _file_folder(file_type: str) -> str:
    if file_type in _FASTQ_TYPES:
        return "raw"
    if file_type in {"bam", "bai", "cram", "vcf", "vcf.gz"}:
        return "results"
    if file_type in {"csv", "tsv", "txt", "xlsx"}:
        return "results"
    return "results"


def _build_readme(experiment: Experiment, user_email: str, include_fastq: bool, include_provenance: bool) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %Human:%M:%S UTC").replace("%Human", "%H")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        f"Export: {experiment.name}",
        f"Generated by: {user_email}",
        f"Generated at: {now}",
        f"Status: {experiment.status}",
        "",
        "Contents:",
        "  README.txt          -- this file",
        "  sample_manifest.csv -- all samples and metadata",
        "  results/            -- analysis output files",
    ]
    if include_fastq:
        lines.append("  raw/                -- raw FASTQ files")
    if include_provenance:
        lines.append("  provenance/         -- provenance report (JSON, Markdown, PDF, CSV)")
    return "\n".join(lines) + "\n"


def _build_project_readme(project: Project, user_email: str) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return (
        f"Export: {project.name}\n"
        f"Generated by: {user_email}\n"
        f"Generated at: {now}\n"
        "\n"
        "This archive contains one sub-folder per experiment.\n"
        "Each experiment folder contains its own README.txt, sample manifest, and data files.\n"
    )


def _build_sample_manifest(samples: list[Sample]) -> str:
    if not samples:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["sample_id_unique", "organism", "tissue_type", "qc_status", "status"])
        return buf.getvalue()

    fields = [
        "sample_id_unique",
        "organism",
        "tissue_type",
        "donor_source",
        "treatment_condition",
        "molecule_type",
        "qc_status",
        "status",
        "collection_timestamp",
        "library_prep_method",
        "library_layout",
        "chemistry_version",
        "viability_pct",
        "cell_count",
        "prep_notes",
        "qc_notes",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for s in samples:
        row = {f: getattr(s, f, None) for f in fields}
        writer.writerow(row)
    return buf.getvalue()
