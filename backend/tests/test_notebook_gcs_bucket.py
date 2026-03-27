"""Tests for dynamic GCS bucket name in notebook sessions."""

import pytest
from sqlalchemy import text

from app.services.auth_service import AuthService


@pytest.mark.asyncio
async def test_session_uses_configured_working_bucket(session, admin_user):
    """NotebookService passes working_bucket from platform_config to the adapter."""
    from app.services.notebook_service import NotebookService

    # Seed platform_config with a custom working bucket name
    await session.execute(
        text("INSERT INTO platform_config (key, value) VALUES (:k, :v) ON CONFLICT (key) DO UPDATE SET value = :v"),
        {"k": "working_bucket_name", "v": "bioaf-working-myorg-abc123"},
    )
    await session.flush()

    ns = await NotebookService.launch_session(
        session,
        user_id=admin_user.id,
        org_id=admin_user.organization_id,
        session_type="jupyter",
        resource_profile="small",
    )

    # The local adapter stores gcs_home_prefix in the session record
    assert ns.gcs_home_prefix is not None
    assert "bioaf-working-myorg-abc123" in ns.gcs_home_prefix
    assert "bioaf-working/" not in ns.gcs_home_prefix


@pytest.mark.asyncio
async def test_session_falls_back_without_working_bucket(session, admin_user):
    """Without working_bucket_name in config, adapter uses default bucket name."""
    from app.services.notebook_service import NotebookService

    ns = await NotebookService.launch_session(
        session,
        user_id=admin_user.id,
        org_id=admin_user.organization_id,
        session_type="jupyter",
        resource_profile="small",
    )

    assert ns.gcs_home_prefix is not None
    assert "bioaf-working" in ns.gcs_home_prefix


@pytest.mark.asyncio
async def test_ssh_session_uses_configured_working_bucket(session, admin_user):
    """SSH work node sessions also use the configured working bucket."""
    from app.models.session_credential import SessionCredential
    from app.services.notebook_service import NotebookService

    # Seed working bucket
    await session.execute(
        text("INSERT INTO platform_config (key, value) VALUES (:k, :v) ON CONFLICT (key) DO UPDATE SET value = :v"),
        {"k": "working_bucket_name", "v": "bioaf-working-custom-xyz"},
    )

    # SSH sessions require session credentials
    cred = SessionCredential(
        user_id=admin_user.id,
        organization_id=admin_user.organization_id,
        username="testadmin",
        password_hash=AuthService.hash_password("testpass123"),
    )
    session.add(cred)
    await session.flush()

    ns = await NotebookService.launch_session(
        session,
        user_id=admin_user.id,
        org_id=admin_user.organization_id,
        session_type="ssh",
        resource_profile="small",
    )

    assert ns.gcs_home_prefix is not None
    assert "bioaf-working-custom-xyz" in ns.gcs_home_prefix


@pytest.mark.asyncio
async def test_rstudio_session_uses_configured_working_bucket(session, admin_user):
    """RStudio sessions also use the configured working bucket."""
    from app.models.session_credential import SessionCredential
    from app.services.notebook_service import NotebookService

    await session.execute(
        text("INSERT INTO platform_config (key, value) VALUES (:k, :v) ON CONFLICT (key) DO UPDATE SET value = :v"),
        {"k": "working_bucket_name", "v": "bioaf-working-rstudio-test"},
    )

    cred = SessionCredential(
        user_id=admin_user.id,
        organization_id=admin_user.organization_id,
        username="rstudiouser",
        password_hash=AuthService.hash_password("testpass123"),
    )
    session.add(cred)
    await session.flush()

    ns = await NotebookService.launch_session(
        session,
        user_id=admin_user.id,
        org_id=admin_user.organization_id,
        session_type="rstudio",
        resource_profile="small",
    )

    assert ns.gcs_home_prefix is not None
    assert "bioaf-working-rstudio-test" in ns.gcs_home_prefix
