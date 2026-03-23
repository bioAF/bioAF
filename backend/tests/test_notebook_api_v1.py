"""Tests for Phase 22 notebook API endpoints (tests 20-29 from spec).

Uses /api/v1/notebooks/sessions and /api/v1/settings/notebooks routes.
"""

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.services.auth_service import AuthService


@pytest_asyncio.fixture
async def comp_bio_user(session, admin_user):
    from app.models.user import User

    password_hash = AuthService.hash_password("compbiopass123")
    user = User(
        email="compbio-api@test.com",
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
async def bench_user(session, admin_user):
    from app.models.user import User

    password_hash = AuthService.hash_password("benchpass123")
    user = User(
        email="bench-api@test.com",
        password_hash=password_hash,
        role_id=admin_user._test_role_map["bench"],
        organization_id=admin_user.organization_id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return user


@pytest_asyncio.fixture
async def bench_token(bench_user) -> str:
    return AuthService.create_token(
        bench_user.id, bench_user.email, bench_user.role_id, bench_user.organization_id, role_name="bench"
    )


@pytest_asyncio.fixture
async def platform_config_ready(session):
    """Seed platform_config so notebook launch passes precondition checks."""
    await session.execute(
        text("""
            INSERT INTO platform_config (key, value) VALUES
                ('compute_deployed', 'true'),
                ('bioaf_scrna_image', 'us-central1-docker.pkg.dev/proj/repo/bioaf-scrna:latest')
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """)
    )
    await session.commit()


# Test 20: Launch requires comp_bio or admin
@pytest.mark.asyncio
async def test_launch_requires_comp_bio_or_admin(client, bench_token, platform_config_ready):
    response = await client.post(
        "/api/v1/notebooks/sessions",
        json={"session_type": "jupyter", "resource_profile": "small"},
        headers={"Authorization": f"Bearer {bench_token}"},
    )
    assert response.status_code == 403


# Test 21: Launch requires compute_deployed
@pytest.mark.asyncio
async def test_launch_requires_compute_deployed(client, session, comp_bio_token):
    await session.execute(
        text("""
            INSERT INTO platform_config (key, value) VALUES
                ('compute_deployed', 'false'),
                ('bioaf_scrna_image', 'some-image:latest')
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """)
    )
    await session.commit()

    response = await client.post(
        "/api/v1/notebooks/sessions",
        json={"session_type": "jupyter", "resource_profile": "small"},
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 400


# Test 22: Launch requires image configured
@pytest.mark.asyncio
async def test_launch_requires_image_configured(client, session, comp_bio_token):
    await session.execute(
        text("""
            INSERT INTO platform_config (key, value) VALUES
                ('compute_deployed', 'true'),
                ('bioaf_scrna_image', 'null')
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """)
    )
    await session.commit()

    response = await client.post(
        "/api/v1/notebooks/sessions",
        json={"session_type": "jupyter", "resource_profile": "small"},
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 400
    assert "image" in response.json()["detail"].lower()


# Test 23: Launch creates session
@pytest.mark.asyncio
async def test_launch_creates_session(client, comp_bio_token, platform_config_ready):
    response = await client.post(
        "/api/v1/notebooks/sessions",
        json={"session_type": "jupyter", "resource_profile": "small"},
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code in (200, 201)
    data = response.json()
    assert data["session_type"] == "jupyter"
    assert data["status"] in ("starting", "pending", "running")


# Test 24: Stop requires owner or admin
@pytest.mark.asyncio
async def test_stop_requires_owner_or_admin(client, session, comp_bio_user, comp_bio_token, admin_user):
    from app.models.notebook_session import NotebookSession

    # Create session owned by admin
    ns = NotebookSession(
        user_id=admin_user.id,
        organization_id=admin_user.organization_id,
        session_type="jupyter",
        resource_profile="small",
        cpu_cores=2,
        memory_gb=4,
        status="running",
    )
    session.add(ns)
    await session.flush()
    await session.commit()

    # comp_bio tries to stop admin's session
    response = await client.post(
        f"/api/v1/notebooks/sessions/{ns.id}/stop",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 403


# Test 25: Admin can stop any session
@pytest.mark.asyncio
async def test_stop_by_admin_succeeds(client, session, comp_bio_user, admin_token):
    from app.models.notebook_session import NotebookSession

    ns = NotebookSession(
        user_id=comp_bio_user.id,
        organization_id=comp_bio_user.organization_id,
        session_type="jupyter",
        resource_profile="small",
        cpu_cores=2,
        memory_gb=4,
        status="running",
    )
    session.add(ns)
    await session.flush()
    await session.commit()

    response = await client.post(
        f"/api/v1/notebooks/sessions/{ns.id}/stop",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "stopped"


# Test 26: List sessions filtered by role
@pytest.mark.asyncio
async def test_list_sessions_filtered_by_role(client, session, comp_bio_user, comp_bio_token, admin_user, admin_token):
    from app.models.notebook_session import NotebookSession

    for uid in [admin_user.id, comp_bio_user.id]:
        ns = NotebookSession(
            user_id=uid,
            organization_id=admin_user.organization_id,
            session_type="jupyter",
            resource_profile="small",
            cpu_cores=2,
            memory_gb=4,
            status="running",
        )
        session.add(ns)
    await session.flush()
    await session.commit()

    # Admin sees all
    resp_admin = await client.get(
        "/api/v1/notebooks/sessions",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp_admin.status_code == 200
    assert resp_admin.json()["total"] >= 2

    # Comp bio sees only own
    resp_cb = await client.get(
        "/api/v1/notebooks/sessions",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert resp_cb.status_code == 200
    for s in resp_cb.json()["sessions"]:
        assert s["user"]["id"] == comp_bio_user.id


# Test 27: Sync triggers GCS sync
@pytest.mark.asyncio
async def test_sync_triggers_gcs_sync(client, session, comp_bio_user, comp_bio_token):
    from app.models.notebook_session import NotebookSession

    ns = NotebookSession(
        user_id=comp_bio_user.id,
        organization_id=comp_bio_user.organization_id,
        session_type="jupyter",
        resource_profile="small",
        cpu_cores=2,
        memory_gb=4,
        status="running",
        k8s_pod_name="bioaf-notebook-sync-test",
        k8s_namespace="bioaf-notebooks",
        gcs_home_prefix=f"gs://bioaf-working/notebooks/{comp_bio_user.id}/",
    )
    session.add(ns)
    await session.flush()
    await session.commit()

    response = await client.post(
        f"/api/v1/notebooks/sessions/{ns.id}/sync",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    # Should succeed (sync is best-effort in local mode)
    assert response.status_code == 200


# Test 28: Notebook settings requires admin
@pytest.mark.asyncio
async def test_notebook_settings_requires_admin(client, comp_bio_token):
    response = await client.put(
        "/api/v1/settings/notebooks",
        json={"idle_timeout_hours": 6, "idle_warning_minutes": 10, "max_sessions_per_user": 3},
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 403


# Test 29: Container registry config
@pytest.mark.asyncio
async def test_container_registry_config(client, session, admin_token):
    response = await client.put(
        "/api/v1/settings/container-registry",
        json={"bioaf_scrna_image": "us-central1-docker.pkg.dev/proj/repo/bioaf-scrna:v2"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200

    result = await session.execute(text("SELECT value FROM platform_config WHERE key = 'bioaf_scrna_image'"))
    val = result.scalar_one_or_none()
    assert val == "us-central1-docker.pkg.dev/proj/repo/bioaf-scrna:v2"
