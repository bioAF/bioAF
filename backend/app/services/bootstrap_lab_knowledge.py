"""Bootstrap helper: seed the default Lab Knowledge vocabularies per org.

ADR-060 seeds a default lab-document tag vocabulary on org creation; ADR-063
seeds default SDR categories. These run from the org bootstrap path so a newly
provisioned org starts with the vocabularies ready to use. The Alembic migration
backfills the same defaults for orgs that predate the feature.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lab_document import LabDocumentTag

DEFAULT_DOCUMENT_TAGS = ["manual", "contact", "procedure", "policy", "standard"]


async def seed_default_lab_document_tags(session: AsyncSession, org_id: int, user_id: int) -> None:
    """Seed the default document tag vocabulary for an org (idempotent)."""
    existing = await session.execute(
        select(LabDocumentTag.name).where(LabDocumentTag.organization_id == org_id)
    )
    have = {row[0] for row in existing.fetchall()}
    for name in DEFAULT_DOCUMENT_TAGS:
        if name in have:
            continue
        session.add(LabDocumentTag(organization_id=org_id, name=name, created_by_user_id=user_id))
    await session.flush()
