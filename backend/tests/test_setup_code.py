"""Tests for setup_code_hash and setup_code_expires_at columns on Organization."""

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


async def test_organization_has_setup_code_hash_column(session):
    """Organization table has a setup_code_hash column."""
    result = await session.execute(
        text(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name = 'organizations' AND column_name = 'setup_code_hash'"
        )
    )
    row = result.fetchone()
    assert row is not None, "setup_code_hash column does not exist"
    assert row[1] == "character varying"
    assert row[2] == "YES"  # nullable


async def test_organization_has_setup_code_expires_at_column(session):
    """Organization table has a setup_code_expires_at column."""
    result = await session.execute(
        text(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name = 'organizations' AND column_name = 'setup_code_expires_at'"
        )
    )
    row = result.fetchone()
    assert row is not None, "setup_code_expires_at column does not exist"
    assert row[1] == "timestamp with time zone"
    assert row[2] == "YES"  # nullable


async def test_setup_code_columns_default_to_null(session):
    """Both setup code columns default to NULL on new organizations."""
    from app.models.organization import Organization

    org = Organization(name="Test Org For Setup Code")
    session.add(org)
    await session.flush()

    result = await session.execute(
        text("SELECT setup_code_hash, setup_code_expires_at FROM organizations WHERE id = :id"),
        {"id": org.id},
    )
    row = result.fetchone()
    assert row is not None
    assert row[0] is None, "setup_code_hash should default to NULL"
    assert row[1] is None, "setup_code_expires_at should default to NULL"
