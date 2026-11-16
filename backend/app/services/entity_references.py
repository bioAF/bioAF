"""Identity and relationship snapshots shared by webhook payloads and entity responses.

Every integration-facing entity has a bioAF ID (``experiment_id``, ``sample_id``, ``file_id``) and a
caller-supplied ``external_id`` that is JSON null when none was assigned. A related record is named
by an explicit pair (``project_id``/``project_external_id``, ``experiment_id``/
``experiment_external_id``); an absent relationship has both null. A subject's ``external_id`` is
always its own, never a parent's.

Builders load trusted scalar values in batches and return plain dicts, so a snapshot taken at
mutation time cannot change when the records do later. Relationships come from persisted
associations only, and every related record must belong to the subject's organization: an
inconsistent link raises ``InconsistentReference`` without naming the foreign identifiers.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.experiment import Experiment
from app.models.file import File
from app.models.project import Project
from app.models.sample import Sample


class InconsistentReference(Exception):
    """A relationship crosses an organization boundary or names a missing record."""


async def _project_external_ids(session: AsyncSession, project_ids: Iterable[int], org_id: int) -> dict[int, Any]:
    ids = {p for p in project_ids if p is not None}
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(Project.id, Project.external_id, Project.organization_id).where(Project.id.in_(ids))
        )
    ).all()
    found = {}
    for pid, ext, owner in rows:
        if owner != org_id:
            raise InconsistentReference("a related project belongs to another organization")
        found[pid] = ext
    if set(found) != ids:
        raise InconsistentReference("a related project does not exist")
    return found


async def _experiments(session: AsyncSession, experiment_ids: Iterable[int], org_id: int) -> dict[int, tuple]:
    ids = {e for e in experiment_ids if e is not None}
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(Experiment.id, Experiment.external_id, Experiment.project_id, Experiment.organization_id).where(
                Experiment.id.in_(ids)
            )
        )
    ).all()
    found = {}
    for eid, ext, project_id, owner in rows:
        if owner != org_id:
            raise InconsistentReference("a related experiment belongs to another organization")
        found[eid] = (ext, project_id)
    if set(found) != ids:
        raise InconsistentReference("a related experiment does not exist")
    return found


async def experiment_reference(session: AsyncSession, experiment: Experiment) -> dict[str, Any]:
    """``{experiment_id, external_id, project_id, project_external_id}`` for one experiment."""
    projects = await _project_external_ids(session, [experiment.project_id], experiment.organization_id)
    return {
        "experiment_id": experiment.id,
        "external_id": experiment.external_id,
        "project_id": experiment.project_id,
        "project_external_id": projects.get(experiment.project_id) if experiment.project_id is not None else None,
    }


async def sample_references(
    session: AsyncSession, sample_ids: Iterable[int], org_id: int | None = None
) -> dict[int, dict[str, Any]]:
    """``sample_id -> {sample_id, external_id, experiment_id, experiment_external_id}``, batch-loaded.

    With ``org_id``, every sample's experiment must belong to that organization."""
    ids = sorted({s for s in sample_ids if s is not None})
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(
                Sample.id, Sample.external_id, Sample.experiment_id, Experiment.external_id, Experiment.organization_id
            )
            .join(Experiment, Experiment.id == Sample.experiment_id)
            .where(Sample.id.in_(ids))
        )
    ).all()
    refs: dict[int, dict[str, Any]] = {}
    for sid, ext, exp_id, exp_ext, owner in rows:
        if org_id is not None and owner != org_id:
            raise InconsistentReference("a linked sample belongs to another organization")
        refs[sid] = {"sample_id": sid, "external_id": ext, "experiment_id": exp_id, "experiment_external_id": exp_ext}
    if set(refs) != set(ids):
        raise InconsistentReference("a linked sample does not exist")
    return refs


async def file_relationships(session: AsyncSession, file: File) -> dict[str, Any]:
    """The file's project/experiment pairs and sample references, as persisted now.

    The project is the file's own, else its experiment's. ``sample_ids`` is sorted and unique, and
    ``samples`` lists the same IDs in the same order."""
    org_id = file.organization_id
    experiments = await _experiments(session, [file.experiment_id], org_id)
    experiment_external_id = None
    project_id = file.project_id
    if file.experiment_id is not None:
        experiment_external_id, experiment_project = experiments[file.experiment_id]
        if project_id is None:
            project_id = experiment_project
    projects = await _project_external_ids(session, [project_id], org_id)

    linked = (
        (await session.execute(text("SELECT sample_id FROM sample_files WHERE file_id = :f"), {"f": file.id}))
        .scalars()
        .all()
    )
    samples = await sample_references(session, linked, org_id)
    sample_ids = sorted(samples)
    return {
        "project_id": project_id,
        "project_external_id": projects.get(project_id) if project_id is not None else None,
        "experiment_id": file.experiment_id,
        "experiment_external_id": experiment_external_id,
        "sample_ids": sample_ids,
        "samples": [samples[sid] for sid in sample_ids],
    }
