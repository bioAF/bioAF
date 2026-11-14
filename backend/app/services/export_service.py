"""Data exports for experiments and projects.

An export is one ZIP holding the selected catalogue files' actual bytes, a README, sample manifests
and, when requested, provenance reports, under the layout in ``data_export/naming.py``. It is
delivered directly (streamed from private scratch) or through a time-limited signed link.

The order of work is the contract (plan 24):

1. Scope and select (``data_export/selection.py``): the entity must belong to the organization;
   membership, deduplication and FASTQ rules are the same as the estimate's.
2. Refuse what cannot succeed before any content work: over-limit selections (413), selected files
   whose stored bytes are known to be gone (409).
3. Admit (``data_export/resources.py``): take the one global slot (429 when busy) and reserve
   scratch above the free-space floor (503).
4. Generate reports, then build the archive (``data_export/archive.py``). A requested provenance
   report that cannot be generated fails the export; nothing is silently omitted.
5. Hand the prepared archive to the caller, who owns it until delivery ends.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import DomainError, ExportContentUnavailableError, NotFoundError
from app.services.data_export import naming, resources, selection
from app.services.data_export.archive import ArchivePlan, ExportJob
from app.services.data_export.selection import ExperimentContent, ProjectContent
from app.services.provenance.report_service import ProvenanceReportService

logger = logging.getLogger("bioaf.export")

# ---------------------------------------------------------------------------
# Size estimation
# ---------------------------------------------------------------------------


async def estimate_experiment_export(
    session: AsyncSession,
    experiment_id: int,
    org_id: int,
    include_fastq: bool = False,
) -> dict | None:
    """Return total_bytes and per-category breakdown for an experiment export."""
    content = await selection.select_experiment(
        session, experiment_id, org_id, include_fastq=include_fastq, manifests=False
    )
    return None if content is None else selection.experiment_estimate(content)


async def estimate_project_export(
    session: AsyncSession,
    project_id: int,
    org_id: int,
    include_fastq: bool = False,
) -> dict | None:
    """Return total_bytes, per-category breakdown, and per-experiment breakdown.

    Files in the project but in none of its experiments count toward the totals without a row.
    """
    content = await selection.select_project(session, project_id, org_id, include_fastq=include_fastq, manifests=False)
    return None if content is None else selection.project_estimate(content)


# ---------------------------------------------------------------------------
# Prepared archives
# ---------------------------------------------------------------------------


@dataclass
class PreparedExport:
    """A finished archive in private scratch. Its owner must ``release`` it exactly once (idempotent)."""

    job: ExportJob
    size: int
    filename: str

    @property
    def path(self) -> str:
        return str(self.job.archive_path)

    def release(self) -> None:
        self.job.release()


async def prepare_experiment_export(
    session: AsyncSession,
    experiment_id: int,
    org_id: int,
    *,
    include_fastq: bool,
    include_provenance: bool,
    user_email: str,
) -> PreparedExport:
    content = await selection.select_experiment(
        session, experiment_id, org_id, include_fastq=include_fastq, limit=resources.MAX_FILES + 1
    )
    if content is None:
        raise NotFoundError(f"Experiment {experiment_id} not found")
    plan = ArchivePlan()
    _plan_experiment(plan, content, content.root, user_email, include_fastq, include_provenance)

    async def reports() -> None:
        if include_provenance:
            plan.add_generated(*await _provenance(session, "experiment", content.id, org_id, user_email, content.root))

    return await _prepare(plan, list(content.files), include_fastq, reports, _filename(content.name, "experiment"))


async def prepare_project_export(
    session: AsyncSession,
    project_id: int,
    org_id: int,
    *,
    include_fastq: bool,
    include_provenance: bool,
    user_email: str,
) -> PreparedExport:
    content = await selection.select_project(
        session, project_id, org_id, include_fastq=include_fastq, limit=resources.MAX_FILES + 1
    )
    if content is None:
        raise NotFoundError(f"Project {project_id} not found")
    plan = ArchivePlan()
    root = content.root
    plan.add_generated(f"{root}/README.txt", _build_project_readme(content, user_email))
    for experiment in content.experiments:
        _plan_experiment(
            plan, experiment, f"{root}/experiments/{experiment.root}", user_email, include_fastq, include_provenance
        )
    for file in content.files:
        plan.add_file(root, file)

    async def reports() -> None:
        if not include_provenance:
            return
        for experiment in content.experiments:
            exp_root = f"{root}/experiments/{experiment.root}"
            plan.add_generated(*await _provenance(session, "experiment", experiment.id, org_id, user_email, exp_root))
        plan.add_generated(*await _provenance(session, "project", content.id, org_id, user_email, root))

    return await _prepare(plan, list(content.all_files), include_fastq, reports, _filename(content.name, "project"))


async def _prepare(plan: ArchivePlan, files, include_fastq: bool, reports, filename: str) -> PreparedExport:
    from app.adapters.registry import get_storage_adapter

    sizes = [f.size_bytes for f in files]
    resources.check_source_limits(sizes, fastq_omitted=not include_fastq)
    job = ExportJob.admit(sizes, scratch_dir=settings.export_scratch_dir)
    try:
        await reports()
    except BaseException:
        job.release()
        raise
    size = await job.build(plan, get_storage_adapter())
    return PreparedExport(job=job, size=size, filename=filename)


def _plan_experiment(
    plan: ArchivePlan,
    content: ExperimentContent,
    root: str,
    user_email: str,
    include_fastq: bool,
    include_provenance: bool,
) -> None:
    plan.add_generated(f"{root}/README.txt", _build_readme(content, user_email, include_fastq, include_provenance))
    plan.add_generated(f"{root}/sample_manifest.csv", content.sample_manifest)
    for file in content.files:
        plan.add_file(root, file)


async def _provenance(
    session: AsyncSession, entity_type: str, entity_id: int, org_id: int, user_email: str, root: str
) -> tuple[str, bytes]:
    """The requested provenance report as an archive member; failing to make it fails the export."""
    try:
        report = await ProvenanceReportService.generate(
            session=session,
            entity_type=entity_type,
            entity_id=entity_id,
            org_id=org_id,
            user_email=user_email,
            format="all",
        )
    except Exception as exc:
        if isinstance(exc, DomainError) and not isinstance(exc, NotFoundError):
            raise
        logger.warning("Export provenance report failed for %s %d (%s)", entity_type, entity_id, type(exc).__name__)
        raise ExportContentUnavailableError(
            "The provenance report could not be generated, so the export was stopped. "
            "Try again, or export without the provenance report."
        ) from exc
    content = report.content.encode("utf-8") if isinstance(report.content, str) else report.content
    name = naming.safe_component(report.filename, fallback=f"provenance_{entity_type}")
    return naming.join(*root.split("/"), "provenance", name), content


def _filename(name: str, kind: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{naming.safe_component(name, fallback=kind, max_bytes=96)}_export_{ts}.zip"


# ---------------------------------------------------------------------------
# Generated content
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _build_readme(experiment: ExperimentContent, user_email: str, include_fastq: bool, include_provenance: bool) -> str:
    lines = [
        f"Export: {experiment.name}",
        f"Experiment UUID: {experiment.uuid}",
        f"Generated by: {user_email}",
        f"Generated at: {_now()}",
        f"Status: {experiment.status}",
        "",
        "Contents:",
        "  README.txt          -- this file",
        "  sample_manifest.csv -- all samples and metadata",
        "  results/            -- analysis output files, one folder per catalogue file UUID",
    ]
    if include_fastq:
        lines.append("  raw/                -- raw FASTQ files, one folder per catalogue file UUID")
    if include_provenance:
        lines.append("  provenance/         -- provenance report (JSON, Markdown, PDF, CSV)")
    return "\n".join(lines) + "\n"


def _build_project_readme(project: ProjectContent, user_email: str) -> str:
    return (
        f"Export: {project.name}\n"
        f"Project UUID: {project.uuid}\n"
        f"Generated by: {user_email}\n"
        f"Generated at: {_now()}\n"
        "\n"
        "experiments/ holds one folder per experiment, named by its UUID and name.\n"
        "Each experiment folder contains its own README.txt, sample manifest, and data files.\n"
        "A file linked to several experiments appears once, under the experiment it is attached to\n"
        "(or the first by UUID). Files that belong to the project but to none of its experiments\n"
        "are in results/ and raw/ at this level.\n"
    )
