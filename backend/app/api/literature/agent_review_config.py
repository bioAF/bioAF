"""Agent Review literature config: what literature feeds into Agent Review.

Org-level and per-scope (experiment/project) toggles for abstracts, comments,
full text, and the token budget injected into the Agent Review artifact
(ADR-057).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.models.literature import AgentReviewLiteratureConfig
from app.schemas.literature import LiteratureConfigPayload, LiteratureConfigUpdateRequest

router = APIRouter()


async def _get_literature_config(
    session: AsyncSession, *, org_id: int, scope_type: str, scope_id: int | None
) -> AgentReviewLiteratureConfig | None:
    query = select(AgentReviewLiteratureConfig).where(
        AgentReviewLiteratureConfig.organization_id == org_id,
        AgentReviewLiteratureConfig.scope_type == scope_type,
    )
    if scope_id is None:
        query = query.where(AgentReviewLiteratureConfig.scope_id.is_(None))
    else:
        query = query.where(AgentReviewLiteratureConfig.scope_id == scope_id)
    return (await session.execute(query)).scalar_one_or_none()


def _serialize_literature_config(
    row: AgentReviewLiteratureConfig | None, *, scope_type: str, scope_id: int | None
) -> LiteratureConfigPayload:
    if row is None:
        return LiteratureConfigPayload(
            scope_type=scope_type,
            scope_id=scope_id,
            abstracts_enabled=True,
            comments_enabled=True,
            full_text_enabled=False,
            max_tokens=100_000,
        )
    return LiteratureConfigPayload(
        scope_type=row.scope_type,
        scope_id=row.scope_id,
        abstracts_enabled=row.abstracts_enabled,
        comments_enabled=row.comments_enabled,
        full_text_enabled=row.full_text_enabled,
        max_tokens=row.max_tokens,
    )


@router.get("/agent-review-config", response_model=LiteratureConfigPayload)
async def get_org_literature_config_endpoint(
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    row = await _get_literature_config(session, org_id=org_id, scope_type="org", scope_id=None)
    return _serialize_literature_config(row, scope_type="org", scope_id=None)


@router.put("/agent-review-config", response_model=LiteratureConfigPayload)
async def update_org_literature_config_endpoint(
    body: LiteratureConfigUpdateRequest,
    current_user: dict = require_permission("literature", "configure_sources"),
    session: AsyncSession = Depends(get_session),
):
    return await _upsert_literature_config(
        session=session,
        current_user=current_user,
        scope_type="org",
        scope_id=None,
        body=body,
    )


@router.get("/agent-review-config/{scope_type}/{scope_id}", response_model=LiteratureConfigPayload)
async def get_scoped_literature_config_endpoint(
    scope_type: str,
    scope_id: int,
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    if scope_type not in {"experiment", "project"}:
        raise HTTPException(400, "scope_type must be experiment or project")
    org_id = int(current_user["org_id"])
    row = await _get_literature_config(session, org_id=org_id, scope_type=scope_type, scope_id=scope_id)
    return _serialize_literature_config(row, scope_type=scope_type, scope_id=scope_id)


@router.put("/agent-review-config/{scope_type}/{scope_id}", response_model=LiteratureConfigPayload)
async def update_scoped_literature_config_endpoint(
    scope_type: str,
    scope_id: int,
    body: LiteratureConfigUpdateRequest,
    current_user: dict = require_permission("literature", "configure_sources"),
    session: AsyncSession = Depends(get_session),
):
    if scope_type not in {"experiment", "project"}:
        raise HTTPException(400, "scope_type must be experiment or project")
    return await _upsert_literature_config(
        session=session,
        current_user=current_user,
        scope_type=scope_type,
        scope_id=scope_id,
        body=body,
    )


async def _upsert_literature_config(
    *,
    session: AsyncSession,
    current_user: dict,
    scope_type: str,
    scope_id: int | None,
    body: LiteratureConfigUpdateRequest,
) -> LiteratureConfigPayload:
    from app.services import audit_service

    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    row = await _get_literature_config(session, org_id=org_id, scope_type=scope_type, scope_id=scope_id)
    previous = None
    if row is None:
        row = AgentReviewLiteratureConfig(
            organization_id=org_id,
            scope_type=scope_type,
            scope_id=scope_id,
            abstracts_enabled=True,
            comments_enabled=True,
            full_text_enabled=False,
            max_tokens=100_000,
            updated_by_user_id=user_id,
        )
        session.add(row)
    else:
        previous = {
            "abstracts_enabled": row.abstracts_enabled,
            "comments_enabled": row.comments_enabled,
            "full_text_enabled": row.full_text_enabled,
            "max_tokens": row.max_tokens,
        }
    if body.abstracts_enabled is not None:
        row.abstracts_enabled = body.abstracts_enabled
    if body.comments_enabled is not None:
        row.comments_enabled = body.comments_enabled
    if body.full_text_enabled is not None:
        row.full_text_enabled = body.full_text_enabled
    if body.max_tokens is not None:
        if body.max_tokens < 1000 or body.max_tokens > 1_000_000:
            raise HTTPException(400, "max_tokens must be between 1000 and 1000000")
        row.max_tokens = body.max_tokens
    row.updated_by_user_id = user_id
    await session.flush()
    await audit_service.log_action(
        session,
        user_id=user_id,
        entity_type="agent_review_literature_config",
        entity_id=row.id,
        action="update",
        details={
            "scope_type": scope_type,
            "scope_id": scope_id,
            "abstracts_enabled": row.abstracts_enabled,
            "comments_enabled": row.comments_enabled,
            "full_text_enabled": row.full_text_enabled,
            "max_tokens": row.max_tokens,
        },
        previous_value=previous,
    )
    await session.commit()
    return _serialize_literature_config(row, scope_type=scope_type, scope_id=scope_id)
