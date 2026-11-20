"""Which candidate files a GEO export references, decided by Sample links (``sample_files``) alone.

Candidates are the export's existing scope (the Organization's files stored under the Experiment's
``/experiments/{id}/`` prefix); links only filter that set, they never widen it.

- Linked to a selected Sample: referenced once export-wide, and by each selected Sample linked to it.
- Linked, but to no selected Sample (excluded Samples, or another Experiment's): omitted.
- No links anywhere: an Experiment-level asset, referenced export-wide and by no Sample.

Shared and unlinked files are referenced whole; their contents are never inspected. Filename resemblance
is never an ownership rule.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import ColumnElement, and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import GeoAssociationCheckUnavailableError
from app.models.file import File
from app.models.geo_file_association_acknowledgment import GeoFileAssociationAcknowledgment
from app.models.sample import sample_files


logger = logging.getLogger("bioaf.geo.file_associations")

RAW_FILE_TYPES = ("fastq", "fastq.gz", "fq.gz")


def candidate_filter(org_id: int, experiment_id: int) -> ColumnElement[bool]:
    """The export's candidate files: live records of this Organization stored under the Experiment.

    A record deleted from view (``deleted_at``) or whose stored bytes are gone (``storage_deleted``) is
    never a candidate, so it is neither referenced, nor reported as a finding, nor acknowledgeable.
    Shared by gathering and acknowledgment; the advisory's SQL mirrors it (association_advisory).
    """
    return and_(
        File.organization_id == org_id,
        File.storage_uri.contains(f"/experiments/{experiment_id}/"),
        File.deleted_at.is_(None),
        File.storage_deleted.is_(False),
    )


@dataclass
class FileSelection:
    files: list[File]
    """Export-wide references, each once, in candidate order."""
    per_sample: dict[int, list[File]] = field(default_factory=dict)
    """Selected Sample id -> the files linked to it."""
    unlinked: list[File] = field(default_factory=list)
    """Candidates with no Sample links anywhere (Experiment-level assets)."""


async def load_links(session: AsyncSession, file_ids: list[int]) -> dict[int, set[int]]:
    """Every Sample each file is linked to, in one query."""
    links: dict[int, set[int]] = defaultdict(set)
    if file_ids:
        rows = await session.execute(
            select(sample_files.c.file_id, sample_files.c.sample_id).where(sample_files.c.file_id.in_(file_ids))
        )
        for file_id, sample_id in rows.tuples():
            links[file_id].add(sample_id)
    return links


def select_files(candidates: list[File], links: dict[int, set[int]], selected_sample_ids: set[int]) -> FileSelection:
    selection = FileSelection(files=[], per_sample={sid: [] for sid in selected_sample_ids})
    for f in candidates:
        linked = links.get(f.id, set())
        if not linked:
            selection.files.append(f)
            selection.unlinked.append(f)
            continue
        owners = linked & selected_sample_ids
        if owners:
            selection.files.append(f)
            for sid in owners:
                selection.per_sample[sid].append(f)
    return selection


# --- legacy association findings --------------------------------------------------------------------
#
# One detector for upgrade reporting, readiness, and experiment/SuperSeries download. A finding is an
# unlinked candidate file whose name starts with the external ID of a currently selected Sample (the old
# prefix rule): evidence of a possibly lost per-Sample reference, not proof of ownership. It is resolved
# per file/Sample pair, by a link (the file then is no longer unlinked) or an acknowledgment whose
# fingerprint still matches the current state under the current policy revision.

POLICY_REVISION = 1
"""Bump when the association rules change; every earlier acknowledgment then becomes stale."""


def detect_findings(unlinked: list[File], selected_samples: list[Any]) -> list[tuple[File, list[Any]]]:
    """Each unlinked file with the selected Samples whose external ID its filename starts with."""
    found = []
    for f in unlinked:
        matched = sorted(
            (s for s in selected_samples if s.external_id and f.filename.startswith(s.external_id)),
            key=lambda s: (s.external_id, s.id),
        )
        if matched:
            found.append((f, matched))
    return found


def _digest(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _file_state(f: File, linked_sample_ids: set[int]) -> dict:
    return {"id": f.id, "filename": f.filename, "storage_uri": f.storage_uri, "links": sorted(linked_sample_ids)}


def _sample_state(s: Any) -> list:
    return [s.id, s.experiment_id, s.external_id]


def pair_fingerprint(f: File, linked_sample_ids: set[int], sample: Any) -> str:
    """The state one acknowledgment is valid for."""
    return _digest(
        {"policy": POLICY_REVISION, "file": _file_state(f, linked_sample_ids), "sample": _sample_state(sample)}
    )


def finding_fingerprint(f: File, linked_sample_ids: set[int], samples: list[Any]) -> str:
    """The state a reported finding (all its pairs) was computed from; an acknowledgment must quote it."""
    return _digest(
        {
            "policy": POLICY_REVISION,
            "file": _file_state(f, linked_sample_ids),
            "samples": sorted(_sample_state(s) for s in samples),
        }
    )


@dataclass
class AssociationFinding:
    file: File
    samples: list[Any]
    acknowledged_sample_ids: set[int]
    fingerprint: str

    @property
    def resolved(self) -> bool:
        return all(s.id in self.acknowledged_sample_ids for s in self.samples)

    def as_dict(self) -> dict:
        return {
            "file_id": self.file.id,
            "filename": self.file.filename,
            "file_kind": "raw" if self.file.file_type in RAW_FILE_TYPES else "processed",
            "samples": [
                {
                    "sample_id": s.id,
                    "external_id": s.external_id,
                    "qc_status": s.qc_status,
                    "acknowledged": s.id in self.acknowledged_sample_ids,
                }
                for s in self.samples
            ],
            "resolved": self.resolved,
            "fingerprint": self.fingerprint,
        }


async def associate(
    session: AsyncSession,
    org_id: int,
    experiment_id: int,
    candidates: list[File],
    selected_samples: list[Any],
) -> tuple[FileSelection, list[AssociationFinding]]:
    """The export's file selection and its findings, decided together from the current links.

    Loading links, selecting files and detecting findings are one check: if any part of it cannot run,
    the export is refused with a typed error, never built from a partial or unknown association state.
    """
    try:
        links = await load_links(session, [f.id for f in candidates])
        selection = select_files(candidates, links, {s.id for s in selected_samples})
        findings = await _findings(session, org_id, experiment_id, selection, selected_samples)
        return selection, findings
    except Exception as exc:
        logger.warning("GEO file association check failed for experiment %d: %s", experiment_id, type(exc).__name__)
        raise GeoAssociationCheckUnavailableError(
            "bioAF could not check this experiment's file associations, so the GEO export was not built. "
            "Try again; if it keeps failing, the technical detail is in the application logs."
        ) from exc


async def _findings(
    session: AsyncSession,
    org_id: int,
    experiment_id: int,
    selection: FileSelection,
    selected_samples: list[Any],
) -> list[AssociationFinding]:
    detected = detect_findings(selection.unlinked, selected_samples)
    if not detected:
        return []
    acks = await session.execute(
        select(
            GeoFileAssociationAcknowledgment.file_id,
            GeoFileAssociationAcknowledgment.sample_id,
            GeoFileAssociationAcknowledgment.fingerprint,
        ).where(
            GeoFileAssociationAcknowledgment.organization_id == org_id,
            GeoFileAssociationAcknowledgment.experiment_id == experiment_id,
            GeoFileAssociationAcknowledgment.policy_revision == POLICY_REVISION,
            GeoFileAssociationAcknowledgment.file_id.in_([f.id for f, _ in detected]),
        )
    )
    recorded = {(file_id, sample_id): fp for file_id, sample_id, fp in acks}
    findings = []
    for f, samples in detected:
        acknowledged = {s.id for s in samples if recorded.get((f.id, s.id)) == pair_fingerprint(f, set(), s)}
        findings.append(AssociationFinding(f, samples, acknowledged, finding_fingerprint(f, set(), samples)))
    return findings
