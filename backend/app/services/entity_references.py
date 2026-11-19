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
from typing import cast, Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.experiment import Experiment
from app.models.file import File
from app.models.project import Project
from app.models.sample import Sample


class InconsistentReference(Exception):
    """A relationship crosses an organization boundary or names a missing record."""


async def _project_external_ids(
    session: AsyncSession, project_ids: Iterable[int | None], org_id: int
) -> dict[int, Any]:
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


async def _experiments(session: AsyncSession, experiment_ids: Iterable[int | None], org_id: int) -> dict[int, tuple]:
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


async def file_identity_map(session: AsyncSession, files: list[File]) -> dict[int, dict[str, Any]]:
    """Identity fields for file responses, batch-loaded for a page of files.

    Returns ``file_id -> {external_id, project_external_id, experiment_external_id, sample_ids,
    samples}``. Each parent external ID is paired with the response's existing parent ID (the file's
    own ``project_id``/``experiment_id``). Links that cross an organization boundary are left out of a
    response (and logged) rather than failing the read; event snapshots are strict instead.
    """
    import logging

    log = logging.getLogger("bioaf.entity_references")
    if not files:
        return {}
    file_ids = [f.id for f in files]
    links = (
        await session.execute(
            text("SELECT file_id, sample_id FROM sample_files WHERE file_id = ANY(:ids)").bindparams(ids=file_ids)
        )
    ).all()
    sample_rows = {}
    sample_ids = {sid for _, sid in links}
    if sample_ids:
        sample_rows = {
            row[0]: row
            for row in (
                await session.execute(
                    select(
                        Sample.id,
                        Sample.external_id,
                        Sample.experiment_id,
                        Experiment.external_id,
                        Experiment.organization_id,
                    )
                    .join(Experiment, Experiment.id == Sample.experiment_id)
                    .where(Sample.id.in_(sample_ids))
                )
            ).all()
        }
    experiment_ids = {f.experiment_id for f in files if f.experiment_id is not None}
    experiments = {}
    if experiment_ids:
        experiments = {
            row[0]: row
            for row in (
                await session.execute(
                    select(Experiment.id, Experiment.external_id, Experiment.organization_id).where(
                        Experiment.id.in_(experiment_ids)
                    )
                )
            ).all()
        }
    project_ids = {f.project_id for f in files if f.project_id is not None}
    projects = {}
    if project_ids:
        projects = {
            row[0]: row
            for row in (
                await session.execute(
                    select(Project.id, Project.external_id, Project.organization_id).where(Project.id.in_(project_ids))
                )
            ).all()
        }

    by_file: dict[int, list[int]] = {fid: [] for fid in file_ids}
    for fid, sid in links:
        by_file[cast(int, fid)].append(cast(int, sid))
    result: dict[int, dict[str, Any]] = {}
    for f in files:
        org = f.organization_id
        refs = []
        for sid in sorted(set(by_file[f.id])):
            row = sample_rows.get(sid)
            if row is None or row[4] != org:
                log.warning("File %s links a sample outside its organization; omitted from the response", f.id)
                continue
            refs.append(
                {"sample_id": sid, "external_id": row[1], "experiment_id": row[2], "experiment_external_id": row[3]}
            )
        exp = experiments.get(f.experiment_id)
        proj = projects.get(f.project_id)
        result[f.id] = {
            "external_id": f.external_id,
            "experiment_external_id": exp[1] if exp is not None and exp[2] == org else None,
            "project_external_id": proj[1] if proj is not None and proj[2] == org else None,
            "sample_ids": [r["sample_id"] for r in refs],
            "samples": refs,
        }
    return result


async def verify_association_targets(
    session: AsyncSession,
    org_id: int,
    *,
    project_id: int | None = None,
    experiment_id: int | None = None,
    sample_ids: Iterable[int] = (),
) -> None:
    """Refuse, before anything changes, a file association to a record outside ``org_id``.

    The one check every association entry point runs first (the link operation, every upload path, the
    link service): a missing record and another organization's record both answer "not found", so the
    caller learns nothing about foreign identifiers. Reads only.
    """
    from app.exceptions import NotFoundError

    for what, check in (
        ("Project", lambda: _project_external_ids(session, [project_id], org_id)),
        ("Experiment", lambda: _experiments(session, [experiment_id], org_id)),
        ("Sample", lambda: sample_references(session, sample_ids, org_id)),
    ):
        try:
            await check()
        except InconsistentReference:
            raise NotFoundError(f"{what} not found", plain_body=True) from None
