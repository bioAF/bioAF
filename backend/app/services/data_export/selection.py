"""What an export carries: one deterministic, deduplicated selection captured as plain data.

Membership comes from ``app.services.file_scope`` (the same rules as the file listing). An
experiment export carries its direct and sample-linked files. A project export carries the union
for its experiments plus files attached to the project itself, each catalogue file exactly once
(plan 24 decision 2):

1. under the in-project experiment it is attached to directly; otherwise
2. under the associated in-project experiment with the lowest UUID; otherwise
3. at the project root.

FASTQ files are left out unless asked for. Deleted files and files unrelated to the entity (global
ones included) are never selected. The result holds no ORM objects or session, so the archive
writer can run off the event loop. An estimate reads the same selection; it is not a snapshot of
storage, and the export re-reads the catalogue.
"""

from __future__ import annotations

import csv
import io
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.experiment import Experiment
from app.models.file import File
from app.models.project import Project
from app.models.sample import Sample, sample_files
from app.services import file_scope
from app.services.data_export import naming

FASTQ_TYPES = frozenset({"fastq", "fastq.gz", "fq", "fq.gz"})


@dataclass(frozen=True)
class SelectedFile:
    id: int
    uuid: uuid.UUID
    filename: str
    file_type: str
    size_bytes: int | None
    storage_uri: str | None
    storage_deleted: bool

    @property
    def category(self) -> str:
        return (self.file_type or "other").lower()

    @property
    def folder(self) -> str:
        return "raw" if self.category in FASTQ_TYPES else "results"

    @property
    def member(self) -> str:
        """The file's path below its export root."""
        return naming.data_member(self.folder, self.uuid, self.filename)


@dataclass(frozen=True)
class ExperimentContent:
    id: int
    uuid: uuid.UUID
    name: str
    status: str | None
    sample_manifest: str
    files: tuple[SelectedFile, ...]

    @property
    def root(self) -> str:
        return naming.experiment_root(self.uuid, self.name)


@dataclass(frozen=True)
class ProjectContent:
    id: int
    uuid: uuid.UUID
    name: str
    experiments: tuple[ExperimentContent, ...]
    files: tuple[SelectedFile, ...]  # in the project, in none of its experiments

    @property
    def root(self) -> str:
        return naming.project_root(self.uuid, self.name)

    @property
    def all_files(self) -> tuple[SelectedFile, ...]:
        return tuple(f for e in self.experiments for f in e.files) + self.files


def _selected(file: File) -> SelectedFile:
    return SelectedFile(
        id=file.id,
        uuid=file.uuid,
        filename=file.filename,
        file_type=file.file_type,
        size_bytes=file.size_bytes,
        storage_uri=file.storage_uri,
        storage_deleted=bool(file.storage_deleted),
    )


def _fastq_rule(include_fastq: bool):
    return [] if include_fastq else [func.lower(File.file_type).notin_(FASTQ_TYPES)]


async def _files(session: AsyncSession, membership, org_id: int, include_fastq: bool, limit: int | None):
    query = (
        select(File).where(file_scope.live_files(org_id), membership, *_fastq_rule(include_fastq)).order_by(File.uuid)
    )
    if limit is not None:
        query = query.limit(limit)
    return [_selected(f) for f in (await session.execute(query)).scalars().all()]


# Manifest columns; ``relabeled_from`` is the label migration 080 replaced (empty otherwise).
_MANIFEST_FIELDS = [
    "external_id",
    "relabeled_from",
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
_EMPTY_MANIFEST_FIELDS = ["external_id", "organism", "tissue_type", "qc_status", "status"]


def build_sample_manifest(samples: list[Sample]) -> str:
    buf = io.StringIO()
    if not samples:
        csv.writer(buf).writerow(_EMPTY_MANIFEST_FIELDS)
        return buf.getvalue()
    writer = csv.DictWriter(buf, fieldnames=_MANIFEST_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for s in samples:
        writer.writerow({f: getattr(s, f, None) for f in _MANIFEST_FIELDS})
    return buf.getvalue()


async def _manifest(session: AsyncSession, experiment_id: int, wanted: bool) -> str:
    if not wanted:
        return ""
    samples = (
        (await session.execute(select(Sample).where(Sample.experiment_id == experiment_id).order_by(Sample.id)))
        .scalars()
        .all()
    )
    return build_sample_manifest(list(samples))


async def _experiment_row(session: AsyncSession, experiment_id: int, org_id: int) -> Experiment | None:
    return (
        await session.execute(
            select(Experiment).where(Experiment.id == experiment_id, Experiment.organization_id == org_id)
        )
    ).scalar_one_or_none()


async def select_experiment(
    session: AsyncSession,
    experiment_id: int,
    org_id: int,
    *,
    include_fastq: bool,
    limit: int | None = None,
    manifests: bool = True,
) -> ExperimentContent | None:
    """The experiment's content, or None when the organization has no such experiment.

    ``limit`` bounds the files read (an export passes one past its count ceiling); an estimate
    passes ``manifests=False`` because it needs no sample manifest.
    """
    experiment = await _experiment_row(session, experiment_id, org_id)
    if experiment is None:
        return None
    files = await _files(session, file_scope.experiment_files(experiment_id, org_id), org_id, include_fastq, limit)
    return ExperimentContent(
        id=experiment.id,
        uuid=experiment.uuid,
        name=experiment.name,
        status=experiment.status,
        sample_manifest=await _manifest(session, experiment.id, manifests),
        files=tuple(files),
    )


async def select_project(
    session: AsyncSession,
    project_id: int,
    org_id: int,
    *,
    include_fastq: bool,
    limit: int | None = None,
    manifests: bool = True,
) -> ProjectContent | None:
    """The project's content with each file assigned once, or None for an unknown project."""
    project = (
        await session.execute(select(Project).where(Project.id == project_id, Project.organization_id == org_id))
    ).scalar_one_or_none()
    if project is None:
        return None

    experiments = (
        (
            await session.execute(
                select(Experiment)
                .where(Experiment.project_id == project_id, Experiment.organization_id == org_id)
                .order_by(Experiment.uuid)
            )
        )
        .scalars()
        .all()
    )
    rank = {e.id: e.uuid for e in experiments}

    membership = file_scope.project_files(project_id, org_id)
    rows = (
        (
            await session.execute(
                select(File)
                .where(file_scope.live_files(org_id), membership, *_fastq_rule(include_fastq))
                .order_by(File.uuid)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    links = (
        await session.execute(
            select(sample_files.c.file_id, Sample.experiment_id)
            .join(Sample, Sample.id == sample_files.c.sample_id)
            .where(
                Sample.experiment_id.in_(file_scope.project_experiments(project_id, org_id)),
                sample_files.c.file_id.in_(select(File.id).where(file_scope.live_files(org_id), membership)),
            )
        )
    ).all()
    linked: dict[int, set[int]] = {}
    for file_id, experiment_id in links:
        linked.setdefault(file_id, set()).add(experiment_id)

    assigned: dict[int | None, list[SelectedFile]] = {}
    for row in rows:
        if row.experiment_id in rank:
            owner: int | None = row.experiment_id
        elif linked.get(row.id):
            owner = min(linked[row.id], key=lambda eid: rank[eid])
        else:
            owner = None
        assigned.setdefault(owner, []).append(_selected(row))

    contents = []
    for e in experiments:
        contents.append(
            ExperimentContent(
                id=e.id,
                uuid=e.uuid,
                name=e.name,
                status=e.status,
                sample_manifest=await _manifest(session, e.id, manifests),
                files=tuple(assigned.get(e.id, [])),
            )
        )
    return ProjectContent(
        id=project.id,
        uuid=project.uuid,
        name=project.name,
        experiments=tuple(contents),
        files=tuple(assigned.get(None, [])),
    )


def _breakdown(files) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in files:
        out[f.category] = out.get(f.category, 0) + (f.size_bytes or 0)
    return out


def experiment_estimate(content: ExperimentContent) -> dict:
    breakdown = _breakdown(content.files)
    return {"total_bytes": sum(breakdown.values()), "breakdown": breakdown}


def project_estimate(content: ProjectContent) -> dict:
    breakdown = _breakdown(content.all_files)
    return {
        "total_bytes": sum(breakdown.values()),
        "breakdown": breakdown,
        "experiments": [
            {"experiment_id": e.id, "name": e.name, **experiment_estimate(e)}
            for e in sorted(content.experiments, key=lambda e: e.id)
        ],
    }
