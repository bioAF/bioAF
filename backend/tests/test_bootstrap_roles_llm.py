"""Tests for the LLM RBAC permissions seeded by bootstrap_roles.

Per ADR-053, two new permissions are introduced:
    llm_integration:configure -- admin only at bootstrap.
    llm_integration:use       -- admin and comp_bio at bootstrap.

bench and viewer have neither.
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
async def test_admin_has_both_llm_permissions(db_engine, admin_user):
    async with _factory(db_engine)() as session:
        role_map = admin_user._test_role_map
        assert await _has_perm(session, role_map["admin"], "llm_integration", "configure")
        assert await _has_perm(session, role_map["admin"], "llm_integration", "use")


@pytest.mark.asyncio
async def test_comp_bio_has_use_only(db_engine, admin_user):
    async with _factory(db_engine)() as session:
        role_map = admin_user._test_role_map
        assert not await _has_perm(session, role_map["comp_bio"], "llm_integration", "configure")
        assert await _has_perm(session, role_map["comp_bio"], "llm_integration", "use")


@pytest.mark.asyncio
async def test_bench_and_viewer_have_neither(db_engine, admin_user):
    async with _factory(db_engine)() as session:
        role_map = admin_user._test_role_map
        for role_name in ("bench", "viewer"):
            assert not await _has_perm(session, role_map[role_name], "llm_integration", "configure")
            assert not await _has_perm(session, role_map[role_name], "llm_integration", "use")
