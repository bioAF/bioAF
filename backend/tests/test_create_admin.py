"""Tests for the create_admin CLI module."""

import pytest
import bcrypt
from sqlalchemy import text


@pytest.mark.asyncio
async def test_create_admin_creates_organization(session):
    """create_admin creates an organization with the given name."""
    from app.cli.create_admin import create_admin_user

    await create_admin_user(
        session,
        email="admin@example.com",
        password="SecurePass123!",
        org_name="Acme Biotech",
        org_slug="acme-biotech",
    )

    result = await session.execute(text("SELECT name FROM organizations LIMIT 1"))
    row = result.fetchone()
    assert row is not None
    assert row[0] == "Acme Biotech"


@pytest.mark.asyncio
async def test_create_admin_creates_user(session):
    """create_admin creates a user with admin role and active status."""
    from app.cli.create_admin import create_admin_user

    await create_admin_user(
        session,
        email="admin@example.com",
        password="SecurePass123!",
        org_name="Acme Biotech",
        org_slug="acme-biotech",
    )

    result = await session.execute(
        text(
            "SELECT u.email, r.name, u.status FROM users u "
            "JOIN roles r ON u.role_id = r.id "
            "WHERE u.email = 'admin@example.com'"
        )
    )
    row = result.fetchone()
    assert row is not None
    assert row[0] == "admin@example.com"
    assert row[1] == "admin"
    assert row[2] == "active"


@pytest.mark.asyncio
async def test_create_admin_hashes_password(session):
    """create_admin hashes the password using bcrypt."""
    from app.cli.create_admin import create_admin_user

    await create_admin_user(
        session,
        email="admin@example.com",
        password="SecurePass123!",
        org_name="Acme Biotech",
        org_slug="acme-biotech",
    )

    result = await session.execute(text("SELECT password_hash FROM users WHERE email = 'admin@example.com'"))
    row = result.fetchone()
    assert row is not None
    password_hash = row[0]
    # Verify it is a valid bcrypt hash and matches the password
    assert password_hash.startswith("$2b$")
    assert bcrypt.checkpw("SecurePass123!".encode("utf-8"), password_hash.encode("utf-8"))


@pytest.mark.asyncio
async def test_create_admin_sets_org_slug(session):
    """create_admin sets the org_slug in platform_config."""
    from app.cli.create_admin import create_admin_user

    await create_admin_user(
        session,
        email="admin@example.com",
        password="SecurePass123!",
        org_name="Acme Biotech",
        org_slug="acme-biotech",
    )

    result = await session.execute(text("SELECT value FROM platform_config WHERE key = 'org_slug'"))
    row = result.fetchone()
    assert row is not None
    assert row[0] == "acme-biotech"


@pytest.mark.asyncio
async def test_create_admin_sets_setup_complete(session):
    """create_admin sets setup_complete on the organization."""
    from app.cli.create_admin import create_admin_user

    await create_admin_user(
        session,
        email="admin@example.com",
        password="SecurePass123!",
        org_name="Acme Biotech",
        org_slug="acme-biotech",
    )

    result = await session.execute(text("SELECT setup_complete FROM organizations LIMIT 1"))
    row = result.fetchone()
    assert row is not None
    assert row[0] is True


@pytest.mark.asyncio
async def test_create_admin_idempotent(session):
    """Running create_admin twice with the same email does not error or duplicate."""
    from app.cli.create_admin import create_admin_user

    await create_admin_user(
        session,
        email="admin@example.com",
        password="SecurePass123!",
        org_name="Acme Biotech",
        org_slug="acme-biotech",
    )

    # Second call should not raise
    await create_admin_user(
        session,
        email="admin@example.com",
        password="NewPassword456!",
        org_name="Acme Biotech",
        org_slug="acme-biotech",
    )

    # Should still be exactly one user
    result = await session.execute(text("SELECT count(*) FROM users WHERE email = 'admin@example.com'"))
    assert result.scalar() == 1


@pytest.mark.asyncio
async def test_create_admin_validates_email(session):
    """create_admin rejects invalid email addresses."""
    from app.cli.create_admin import create_admin_user

    with pytest.raises(ValueError, match="[Ii]nvalid email"):
        await create_admin_user(
            session,
            email="not-an-email",
            password="SecurePass123!",
            org_name="Acme Biotech",
            org_slug="acme-biotech",
        )


@pytest.mark.asyncio
async def test_create_admin_validates_slug(session):
    """create_admin rejects invalid org slugs."""
    from app.cli.create_admin import create_admin_user

    with pytest.raises(ValueError, match="[Ii]nvalid.*slug"):
        await create_admin_user(
            session,
            email="admin@example.com",
            password="SecurePass123!",
            org_name="Acme Biotech",
            org_slug="Invalid Slug With Spaces!",
        )
