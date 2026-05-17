"""CRUD for named, user-authored Agent Review prompts (org-wide visibility)."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_review_prompt import AgentReviewPrompt


class DuplicatePromptName(ValueError):
    """Two prompts in the same org may not share a name."""


async def list_for_org(session: AsyncSession, org_id: int) -> Sequence[AgentReviewPrompt]:
    result = await session.execute(
        select(AgentReviewPrompt)
        .where(AgentReviewPrompt.organization_id == org_id)
        .order_by(AgentReviewPrompt.created_at.desc())
    )
    return result.scalars().all()


async def get_for_org(session: AsyncSession, org_id: int, prompt_id: int) -> AgentReviewPrompt | None:
    result = await session.execute(
        select(AgentReviewPrompt).where(
            AgentReviewPrompt.id == prompt_id,
            AgentReviewPrompt.organization_id == org_id,
        )
    )
    return result.scalar_one_or_none()


async def create(
    session: AsyncSession,
    *,
    org_id: int,
    name: str,
    body: str,
    created_by_user_id: int,
) -> AgentReviewPrompt:
    if not name.strip():
        raise ValueError("name is required")
    if not body.strip():
        raise ValueError("body is required")

    existing = await session.execute(
        select(AgentReviewPrompt).where(
            AgentReviewPrompt.organization_id == org_id,
            AgentReviewPrompt.name == name,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise DuplicatePromptName(f"prompt name '{name}' already exists in this org")

    row = AgentReviewPrompt(
        organization_id=org_id,
        name=name,
        body=body,
        created_by_user_id=created_by_user_id,
    )
    session.add(row)
    await session.flush()
    return row


async def delete(
    session: AsyncSession,
    *,
    org_id: int,
    prompt_id: int,
) -> bool:
    row = await get_for_org(session, org_id, prompt_id)
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    return True
