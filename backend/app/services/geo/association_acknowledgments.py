"""Acknowledge that a legacy unlinked file stays Experiment-level for the GEO Samples it was reported against.

Scoped to the reported file/Sample pairs and re-verified against current state on submission: the file must
still be an unlinked candidate of this Experiment in this Organization, every Sample must belong to this
Experiment and still match the file by the old filename rule, and the quoted fingerprint must equal the
current one. Anything else is stale (409) or not found (404); nothing is written in either case.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import GeoAssociationAcknowledgmentStaleError, NotFoundError
from app.models.experiment import Experiment
from app.models.file import File
from app.models.geo_file_association_acknowledgment import GeoFileAssociationAcknowledgment
from app.models.sample import Sample
from app.services.audit_service import log_action
from app.services.geo.file_associations import (
    POLICY_REVISION,
    detect_findings,
    finding_fingerprint,
    load_links,
    pair_fingerprint,
)


async def acknowledge(
    session: AsyncSession,
    *,
    org_id: int,
    experiment_id: int,
    user_id: int,
    file_id: int,
    sample_ids: list[int],
    fingerprint: str,
    reason: str,
    api_key_id: int | None = None,
) -> None:
    experiment = await session.scalar(
        select(Experiment.id).where(Experiment.id == experiment_id, Experiment.organization_id == org_id)
    )
    if experiment is None:
        raise NotFoundError("Experiment not found")
    file = await session.scalar(
        select(File).where(
            File.id == file_id,
            File.organization_id == org_id,
            File.storage_uri.contains(f"/experiments/{experiment_id}/"),
        )
    )
    if file is None:
        raise NotFoundError("File not found")
    wanted = set(sample_ids)
    samples = list(
        (
            await session.scalars(select(Sample).where(Sample.id.in_(wanted), Sample.experiment_id == experiment_id))
        ).all()
    )
    if len(samples) != len(wanted):
        raise NotFoundError("Sample not found")

    stale = GeoAssociationAcknowledgmentStaleError(
        "This file or its Samples changed since the readiness check. Check readiness again, then retry."
    )
    if (await load_links(session, [file.id])).get(file.id):
        raise stale
    detected = detect_findings([file], samples)
    if not detected or len(detected[0][1]) != len(samples):
        raise stale
    if finding_fingerprint(file, set(), samples) != fingerprint:
        raise stale

    for sample in samples:
        values = {
            "organization_id": org_id,
            "experiment_id": experiment_id,
            "file_id": file.id,
            "sample_id": sample.id,
            "policy_revision": POLICY_REVISION,
            "fingerprint": pair_fingerprint(file, set(), sample),
            "reason": reason,
            "acknowledged_by_user_id": user_id,
        }
        stmt = insert(GeoFileAssociationAcknowledgment).values(**values)
        await session.execute(
            stmt.on_conflict_do_update(
                constraint="uq_geo_file_association_ack_pair",
                set_={k: stmt.excluded[k] for k in ("fingerprint", "reason", "acknowledged_by_user_id")},
            )
        )
    await log_action(
        session,
        user_id=user_id,
        entity_type="file",
        entity_id=file.id,
        action="geo_association_acknowledged",
        details={
            "experiment_id": experiment_id,
            "sample_ids": sorted(wanted),
            "policy_revision": POLICY_REVISION,
            "reason": reason,
        },
        api_key_id=api_key_id,
    )
