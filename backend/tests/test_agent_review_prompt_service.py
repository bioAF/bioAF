"""Tests for the agent_review_prompts (named custom prompts) table and service."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.exceptions import ValidationError
from app.services import agent_review_prompt_service as svc


def _factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.mark.asyncio
async def test_create_then_list(db_engine, admin_user):
    async with _factory(db_engine)() as session:
        row = await svc.create(
            session,
            org_id=admin_user.organization_id,
            name="My Standard QC",
            body="body text",
            created_by_user_id=admin_user.id,
        )
        await session.commit()
        assert row.id > 0
        assert row.name == "My Standard QC"
        assert row.body == "body text"

    async with _factory(db_engine)() as session:
        rows = await svc.list_for_org(session, admin_user.organization_id)
        assert len(rows) == 1
        assert rows[0].name == "My Standard QC"


@pytest.mark.asyncio
async def test_create_duplicate_name_rejected(db_engine, admin_user):
    async with _factory(db_engine)() as session:
        await svc.create(
            session,
            org_id=admin_user.organization_id,
            name="duplicate",
            body="b",
            created_by_user_id=admin_user.id,
        )
        await session.commit()

    async with _factory(db_engine)() as session:
        with pytest.raises(svc.DuplicatePromptName):
            await svc.create(
                session,
                org_id=admin_user.organization_id,
                name="duplicate",
                body="b2",
                created_by_user_id=admin_user.id,
            )


@pytest.mark.asyncio
async def test_create_empty_name_or_body_rejected(db_engine, admin_user):
    async with _factory(db_engine)() as session:
        with pytest.raises(ValidationError):
            await svc.create(
                session,
                org_id=admin_user.organization_id,
                name="  ",
                body="body",
                created_by_user_id=admin_user.id,
            )
        with pytest.raises(ValidationError):
            await svc.create(
                session,
                org_id=admin_user.organization_id,
                name="ok",
                body="",
                created_by_user_id=admin_user.id,
            )


@pytest.mark.asyncio
async def test_delete_removes_row(db_engine, admin_user):
    async with _factory(db_engine)() as session:
        row = await svc.create(
            session,
            org_id=admin_user.organization_id,
            name="todelete",
            body="body",
            created_by_user_id=admin_user.id,
        )
        await session.commit()
        deleted = await svc.delete(session, org_id=admin_user.organization_id, prompt_id=row.id)
        await session.commit()
        assert deleted is True

    async with _factory(db_engine)() as session:
        rows = await svc.list_for_org(session, admin_user.organization_id)
        assert rows == []


@pytest.mark.asyncio
async def test_delete_nonexistent_returns_false(db_engine, admin_user):
    async with _factory(db_engine)() as session:
        deleted = await svc.delete(session, org_id=admin_user.organization_id, prompt_id=99999)
        assert deleted is False


@pytest.mark.asyncio
async def test_get_for_org_scoped(db_engine, admin_user):
    async with _factory(db_engine)() as session:
        row = await svc.create(
            session,
            org_id=admin_user.organization_id,
            name="x",
            body="b",
            created_by_user_id=admin_user.id,
        )
        await session.commit()

    async with _factory(db_engine)() as session:
        loaded = await svc.get_for_org(session, admin_user.organization_id, row.id)
        assert loaded is not None and loaded.name == "x"
        # Different org id returns None.
        loaded2 = await svc.get_for_org(session, admin_user.organization_id + 99, row.id)
        assert loaded2 is None
