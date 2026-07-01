"""Tests for the assistant RBAC permission seeded by bootstrap_roles.

The conversational assistant (ai_pipeline_run Phase 1) is gated by a new permission:
    assistant:use -- admin, comp_bio, and bench at bootstrap.

viewer does not get it: a read-only role cannot drive an action-taking agent. Note that
assistant:use only gates *starting* a conversation; every tool the agent calls still
enforces its own underlying resource permission, so the agent never exceeds the user's
existing rights.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.role import RolePermission


def _factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _has_perm(session: AsyncSession, role_id: int, resource: str, action: str) -> bool:
    result = await session.execute(
        select(RolePermission).where(
            RolePermission.role_id == role_id,
            RolePermission.resource == resource,
            RolePermission.action == action,
        )
    )
    return result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_admin_comp_bio_bench_have_assistant_use(db_engine, admin_user):
    async with _factory(db_engine)() as session:
        role_map = admin_user._test_role_map
        for role_name in ("admin", "comp_bio", "bench"):
            assert await _has_perm(session, role_map[role_name], "assistant", "use"), (
                f"{role_name} should have assistant:use"
            )


@pytest.mark.asyncio
async def test_viewer_does_not_have_assistant_use(db_engine, admin_user):
    async with _factory(db_engine)() as session:
        role_map = admin_user._test_role_map
        assert not await _has_perm(session, role_map["viewer"], "assistant", "use")
