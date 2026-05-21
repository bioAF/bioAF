"""Associations: link a Paper to a scope (global, project, experiment).

Mutations log audit entries and run within the caller's transaction.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.literature import (
    ALL_SCOPES,
    LiteratureAssociation,
    LiteraturePaper,
    SCOPE_GLOBAL,
)
from app.services import audit_service


class InvalidScope(Exception):
    pass


class AssociationNotFound(Exception):
    pass


async def list_for_paper(
    session: AsyncSession, paper_id: int, *, include_removed: bool = False
) -> list[LiteratureAssociation]:
    query = select(LiteratureAssociation).where(LiteratureAssociation.paper_id == paper_id)
    if not include_removed:
        query = query.where(LiteratureAssociation.removed_at.is_(None))
    query = query.order_by(LiteratureAssociation.added_at)
    result = await session.execute(query)
    return list(result.scalars().all())


async def get_or_create(
    session: AsyncSession,
    *,
    paper: LiteraturePaper,
    user_id: int,
    scope_type: str,
    scope_id: int | None,
    api_key_id: int | None = None,
) -> LiteratureAssociation:
    if scope_type not in ALL_SCOPES:
        raise InvalidScope(f"unknown scope_type: {scope_type}")
    if scope_type == SCOPE_GLOBAL:
        if scope_id is not None:
            raise InvalidScope("global scope must not carry a scope_id")
    else:
        if scope_id is None:
            raise InvalidScope(f"scope_type={scope_type} requires scope_id")

    query = select(LiteratureAssociation).where(
        LiteratureAssociation.paper_id == paper.id,
        LiteratureAssociation.scope_type == scope_type,
        LiteratureAssociation.removed_at.is_(None),
    )
    if scope_id is None:
        query = query.where(LiteratureAssociation.scope_id.is_(None))
    else:
        query = query.where(LiteratureAssociation.scope_id == scope_id)
    existing = await session.execute(query)
    found = existing.scalar_one_or_none()
    if found is not None:
        return found

    assoc = LiteratureAssociation(
        paper_id=paper.id,
        scope_type=scope_type,
        scope_id=scope_id,
        added_by_user_id=user_id,
    )
    session.add(assoc)
    await session.flush()
    await audit_service.log_action(
        session,
        user_id=user_id,
        api_key_id=api_key_id,
        entity_type="literature_association",
        entity_id=assoc.id,
        action="create",
        details={"paper_id": paper.id, "scope_type": scope_type, "scope_id": scope_id},
    )
    return assoc


async def soft_remove(
    session: AsyncSession,
    *,
    association_id: int,
    user_id: int,
    api_key_id: int | None = None,
) -> LiteratureAssociation:
    result = await session.execute(select(LiteratureAssociation).where(LiteratureAssociation.id == association_id))
    assoc = result.scalar_one_or_none()
    if assoc is None:
        raise AssociationNotFound(f"association {association_id} not found")
    if assoc.removed_at is not None:
        return assoc
    assoc.removed_at = datetime.now(UTC)
    assoc.removed_by_user_id = user_id
    await session.flush()
    await audit_service.log_action(
        session,
        user_id=user_id,
        api_key_id=api_key_id,
        entity_type="literature_association",
        entity_id=assoc.id,
        action="delete",
        details={"paper_id": assoc.paper_id, "scope_type": assoc.scope_type, "scope_id": assoc.scope_id},
    )
    return assoc
