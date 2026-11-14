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

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import DomainError, ExportContentUnavailableError, ExportTimeoutError, NotFoundError
from app.services.data_export import naming, resources, selection
from app.services.data_export.archive import ArchivePlan, ExportJob, check_available
from app.services.data_export.selection import ExperimentContent, ProjectContent
from app.services.provenance.report_service import ProvenanceReportService

logger = logging.getLogger("bioaf.export")

# The manifest builder lives with the selection now; existing callers import it from here.
_build_sample_manifest = selection.build_sample_manifest

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
    content = await selection.select_experiment(session, experiment_id, org_id, include_fastq=include_fastq)
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
    content = await selection.select_project(session, project_id, org_id, include_fastq=include_fastq)
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
    # Set when a response starts streaming it; from then on the response alone releases it.
    claimed: bool = False
    # The timer that releases it if no response ever starts; cancelled once that cannot happen.
    backstop: asyncio.Task | None = None

    @property
    def path(self) -> str:
        return str(self.job.archive_path)

    def _stop_backstop(self) -> None:
        if self.backstop is not None and self.backstop is not asyncio.current_task():
            self.backstop.cancel()
        self.backstop = None

    def claim(self) -> None:
        """A response started streaming this export and now owns its release."""
        self.claimed = True
        self._stop_backstop()

    def release(self) -> None:
        self.job.release()
        self._stop_backstop()


async def prepare_experiment_export(
    session: AsyncSession,
    experiment_id: int,
    org_id: int,
    *,
    include_fastq: bool,
    include_provenance: bool,
    user_email: str,
) -> PreparedExport:
    started = asyncio.get_running_loop().time()
    resources.SLOT.refuse_if_busy()
    async with _preparation_deadline(started):
        content = await selection.select_experiment(
            session, experiment_id, org_id, include_fastq=include_fastq, limit=resources.MAX_FILES + 1
        )
    if content is None:
        raise NotFoundError(f"Experiment {experiment_id} not found")

    async def plan(archive: ArchivePlan) -> None:
        await _plan_experiment(
            session, archive, content, content.root, org_id, user_email, include_fastq, include_provenance
        )

    return await _prepare(
        session, list(content.files), include_fastq, plan, _filename(content.name, "experiment"), started
    )


async def prepare_project_export(
    session: AsyncSession,
    project_id: int,
    org_id: int,
    *,
    include_fastq: bool,
    include_provenance: bool,
    user_email: str,
) -> PreparedExport:
    started = asyncio.get_running_loop().time()
    resources.SLOT.refuse_if_busy()
    async with _preparation_deadline(started):
        content = await selection.select_project(
            session,
            project_id,
            org_id,
            include_fastq=include_fastq,
            limit=resources.MAX_FILES + 1,
            max_experiments=resources.MAX_PROJECT_EXPERIMENTS,
        )
    if content is None:
        raise NotFoundError(f"Project {project_id} not found")
    root = content.root

    async def plan(archive: ArchivePlan) -> None:
        archive.add_generated(f"{root}/README.txt", _build_project_readme(content, user_email))
        for experiment in content.experiments:
            exp_root = f"{root}/experiments/{experiment.root}"
            await _plan_experiment(
                session, archive, experiment, exp_root, org_id, user_email, include_fastq, include_provenance
            )
        for file in content.files:
            archive.add_file(root, file)
        if include_provenance:
            archive.add_generated(*await _provenance(session, "project", content.id, org_id, user_email, root))

    return await _prepare(
        session, list(content.all_files), include_fastq, plan, _filename(content.name, "project"), started
    )


@contextlib.asynccontextmanager
async def _preparation_deadline(started: float):
    """The one absolute preparation budget: selection, planning and the build all spend it."""
    try:
        async with asyncio.timeout_at(started + resources.BUILD_DEADLINE):
            yield
    except TimeoutError:
        raise ExportTimeoutError("The export took too long to prepare and was stopped. Try a smaller export.") from None


async def _prepare(session: AsyncSession, files, include_fastq: bool, plan, filename: str, started: float):
    """Refuse what cannot succeed, admit, then plan (manifests, reports) and build under the deadline."""
    from app.adapters.registry import get_storage_adapter

    sizes = [f.size_bytes for f in files]
    resources.check_source_limits(sizes, fastq_omitted=not include_fastq)
    check_available(files)
    job = ExportJob.admit(sizes, scratch_dir=settings.export_scratch_dir)
    archive = ArchivePlan()
    try:
        async with asyncio.timeout_at(started + resources.BUILD_DEADLINE):
            await plan(archive)
            # Nothing below needs the database until the audit: end the read transaction rather
            # than hold a pooled connection idle through a build that may take many minutes.
            await session.commit()
    except TimeoutError:
        job.release()
        raise ExportTimeoutError("The export took too long to prepare and was stopped. Try a smaller export.") from None
    except BaseException:
        job.release()
        raise
    remaining = started + resources.BUILD_DEADLINE - asyncio.get_running_loop().time()
    size = await job.build(archive, get_storage_adapter(), deadline=max(remaining, 0))
    return PreparedExport(job=job, size=size, filename=filename)


async def _plan_experiment(
    session: AsyncSession,
    archive: ArchivePlan,
    content: ExperimentContent,
    root: str,
    org_id: int,
    user_email: str,
    include_fastq: bool,
    include_provenance: bool,
) -> None:
    archive.add_generated(f"{root}/README.txt", _build_readme(content, user_email, include_fastq, include_provenance))
    manifest = await selection.write_sample_manifest(session, content.id, limit_bytes=archive.generated_remaining)
    archive.add_generated(f"{root}/sample_manifest.csv", manifest)
    for file in content.files:
        archive.add_file(root, file)
    if include_provenance:
        archive.add_generated(*await _provenance(session, "experiment", content.id, org_id, user_email, root))


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
