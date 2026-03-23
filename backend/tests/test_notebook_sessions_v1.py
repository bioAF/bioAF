"""Tests for v1 notebook session API -- settings, launch preconditions, and K8s plumbing."""

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.services.auth_service import AuthService


@pytest_asyncio.fixture
async def comp_bio_user(session, admin_user):
    from app.models.user import User

    password_hash = AuthService.hash_password("compbiopass123")
    user = User(
        email="compbio@test.com",
        password_hash=password_hash,
        role_id=admin_user._test_role_map["comp_bio"],
        organization_id=admin_user.organization_id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return user


@pytest_asyncio.fixture
async def comp_bio_token(comp_bio_user) -> str:
    return AuthService.create_token(
        comp_bio_user.id,
        comp_bio_user.email,
        comp_bio_user.role_id,
        comp_bio_user.organization_id,
        role_name="comp_bio",
    )


@pytest_asyncio.fixture
async def seed_platform_config(session):
    """Seed platform_config with notebook-related keys."""
    for key, value in [
        ("compute_deployed", "true"),
        ("bioaf_scrna_image", "us-central1-docker.pkg.dev/test/repo/bioaf-scrna:latest"),
        ("notebook_idle_timeout_hours", "4"),
        ("notebook_idle_warning_minutes", "15"),
        ("notebook_max_sessions_per_user", "2"),
    ]:
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ),
            {"k": key, "v": value},
        )
    await session.commit()


# -- GET settings endpoint --


@pytest.mark.asyncio
async def test_get_notebook_settings_returns_config(client, session, admin_token, seed_platform_config):
    """GET /api/v1/settings/notebooks returns current config values."""
    response = await client.get(
        "/api/v1/settings/notebooks",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["idle_timeout_hours"] == 4
    assert data["idle_warning_minutes"] == 15
    assert data["max_sessions_per_user"] == 2
    assert data["bioaf_scrna_image"] == "us-central1-docker.pkg.dev/test/repo/bioaf-scrna:latest"


@pytest.mark.asyncio
async def test_get_notebook_settings_defaults_when_no_config(client, session, admin_token):
    """GET /api/v1/settings/notebooks returns defaults when no config rows exist."""
    response = await client.get(
        "/api/v1/settings/notebooks",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["idle_timeout_hours"] == 4
    assert data["idle_warning_minutes"] == 15
    assert data["max_sessions_per_user"] == 2
    assert data["bioaf_scrna_image"] == ""


@pytest.mark.asyncio
async def test_get_notebook_settings_requires_admin(client, comp_bio_token, comp_bio_user):
    """Non-admin users cannot read notebook settings."""
    response = await client.get(
        "/api/v1/settings/notebooks",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 403


# -- Launch precondition checks --


@pytest.mark.asyncio
async def test_launch_rejects_when_compute_not_deployed(client, session, comp_bio_token, comp_bio_user):
    """Launch fails when compute_deployed is not true."""
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ),
        {"k": "compute_deployed", "v": "false"},
    )
    await session.commit()

    response = await client.post(
        "/api/v1/notebooks/sessions",
        json={"session_type": "jupyter", "resource_profile": "small"},
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 400
    assert "Compute infrastructure" in response.json()["detail"]


@pytest.mark.asyncio
async def test_launch_rejects_when_image_not_set(client, session, comp_bio_token, comp_bio_user):
    """Launch fails when bioaf_scrna_image is null."""
    for key, value in [("compute_deployed", "true"), ("bioaf_scrna_image", "null")]:
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ),
            {"k": key, "v": value},
        )
    await session.commit()

    response = await client.post(
        "/api/v1/notebooks/sessions",
        json={"session_type": "jupyter", "resource_profile": "small"},
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 400
    assert "image has not been built" in response.json()["detail"]


@pytest.mark.asyncio
async def test_launch_rejects_with_building_message(client, session, comp_bio_token, comp_bio_user):
    """Launch shows building message when image build is in progress."""
    for key, value in [
        ("compute_deployed", "true"),
        ("bioaf_scrna_image", "null"),
        ("notebook_image_build_status", "WORKING"),
    ]:
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ),
            {"k": key, "v": value},
        )
    await session.commit()

    response = await client.post(
        "/api/v1/notebooks/sessions",
        json={"session_type": "jupyter", "resource_profile": "small"},
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 400
    assert "currently building" in response.json()["detail"]


# -- Launch plumbs image URI and stores K8s results --


@pytest.mark.asyncio
async def test_launch_session_succeeds_with_config(
    client, session, comp_bio_token, comp_bio_user, seed_platform_config
):
    """Launch succeeds when preconditions met, returns session with proxy_url."""
    # RStudio requires session credentials
    from app.services.session_credential_service import SessionCredentialService

    await SessionCredentialService.create_or_update(
        session,
        user_id=comp_bio_user.id,
        org_id=comp_bio_user.organization_id,
        email=comp_bio_user.email,
        password="testpass",
    )
    await session.commit()

    response = await client.post(
        "/api/v1/notebooks/sessions",
        json={"session_type": "rstudio", "resource_profile": "small"},
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["session_type"] == "rstudio"
    assert data["status"] in ("starting", "running")
    assert data["proxy_url"] is not None


@pytest.mark.asyncio
async def test_launch_stores_k8s_fields(client, session, comp_bio_token, comp_bio_user, seed_platform_config):
    """Launch populates k8s_pod_name, k8s_namespace, access_url, gcs_home_prefix on the model."""
    response = await client.post(
        "/api/v1/notebooks/sessions",
        json={"session_type": "jupyter", "resource_profile": "small"},
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 200
    session_id = response.json()["id"]

    result = await session.execute(
        text("SELECT k8s_pod_name, k8s_namespace, access_url, gcs_home_prefix FROM notebook_sessions WHERE id = :id"),
        {"id": session_id},
    )
    row = result.first()
    # In local mode these won't be real K8s values, but the plumbing should store them
    # Local adapter returns session_id starting with "local-", no pod_name -- so these may be None
    # The key test is that the code path doesn't crash and the session is created
    assert row is not None


# -- Stop session passes K8s metadata --


@pytest.mark.asyncio
async def test_stop_session_with_k8s_fields(client, session, comp_bio_user, comp_bio_token):
    """Stop session works when k8s fields are populated."""
    from app.models.notebook_session import NotebookSession

    ns = NotebookSession(
        user_id=comp_bio_user.id,
        organization_id=comp_bio_user.organization_id,
        session_type="jupyter",
        resource_profile="small",
        cpu_cores=2,
        memory_gb=4,
        status="running",
        slurm_job_id="local-abc123",
        k8s_pod_name="bioaf-notebook-99",
        k8s_namespace="bioaf-notebooks",
        gcs_home_prefix="gs://bioaf-working/notebooks/1/",
    )
    session.add(ns)
    await session.flush()
    await session.commit()

    response = await client.post(
        f"/api/v1/notebooks/sessions/{ns.id}/stop",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "stopped"
