"""Bootstrap helper: seed the four built-in Literature Sources per org.

ADR-056 mandates that PubMed, bioRxiv, Europe PMC, and Semantic Scholar are
pre-configured for every org with enabled=true by default. This module is
called from the org bootstrap path so newly-provisioned orgs start with the
sources ready to use.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.literature import EXTERNAL_SOURCES, LiteratureSourcesConfig


async def seed_literature_sources(session: AsyncSession, org_id: int) -> None:
    existing = await session.execute(
        select(LiteratureSourcesConfig.source).where(LiteratureSourcesConfig.organization_id == org_id)
    )
    have = {row[0] for row in existing.fetchall()}
    for source in EXTERNAL_SOURCES:
        if source in have:
            continue
        session.add(LiteratureSourcesConfig(organization_id=org_id, source=source, enabled=True))
    await session.flush()
