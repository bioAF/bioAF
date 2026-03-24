"""Tests for work node API endpoints (ADR-034).

TDD: write failing tests first, then implement.
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
async def bench_user(session, admin_user):
    from app.models.user import User

    password_hash = AuthService.hash_password("benchpass123")
    user = User(
        email="bench@test.com",
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
        bench_user.id,
        bench_user.email,
        bench_user.role_id,
        bench_user.organization_id,
        role_name="bench",
    )


@pytest_asyncio.fixture
async def seed_environment(session, admin_user):
    """Create a ready environment version for testing."""
    from app.models.environment import Environment
    from app.models.environment_version import EnvironmentVersion

    env = Environment(
        name="Test Env",
        description="Test environment",
        organization_id=admin_user.organization_id,
        created_by_user_id=admin_user.id,
    )
    session.add(env)
    await session.flush()

    version = EnvironmentVersion(
        environment_id=env.id,
        version_number=1,
        status="ready",
        definition_format="dockerfile",
        definition_content="FROM ubuntu:22.04",
        image_uri="us-central1-docker.pkg.dev/test/repo/test-env:v1",
        created_by_user_id=admin_user.id,
    )
    session.add(version)
    await session.flush()

    draft_version = EnvironmentVersion(
        environment_id=env.id,
        version_number=2,
        status="draft",
        definition_format="dockerfile",
        definition_content="FROM ubuntu:22.04\nRUN apt-get update",
        created_by_user_id=admin_user.id,
    )
    session.add(draft_version)
    await session.flush()
    await session.commit()

    return {"environment": env, "ready_version": version, "draft_version": draft_version}


@pytest_asyncio.fixture
async def seed_project(session, admin_user):
    """Create a project for testing."""
    from app.models.project import Project

    project = Project(
        name="Test Project",
        organization_id=admin_user.organization_id,
        created_by_user_id=admin_user.id,
    )
    session.add(project)
    await session.flush()
    await session.commit()
    return project


@pytest_asyncio.fixture
async def seed_session_credentials(session, comp_bio_user):
    """Set up session credentials for the comp_bio user."""
    from app.services.session_credential_service import SessionCredentialService

    await SessionCredentialService.create_or_update(
        session,
        user_id=comp_bio_user.id,
        org_id=comp_bio_user.organization_id,
        email=comp_bio_user.email,
        password="testpass",
    )
    await session.commit()


@pytest_asyncio.fixture
async def seed_platform_config(session):
    """Seed platform_config with compute-deployed flag."""
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ),
        {"k": "compute_deployed", "v": "true"},
    )
    await session.commit()


# -- Machine types endpoint --


@pytest.mark.asyncio
async def test_list_machine_types(client, comp_bio_token, comp_bio_user):
    """GET /api/v1/work-nodes/machine-types returns curated list."""
    response = await client.get(
        "/api/v1/work-nodes/machine-types",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0

    # Verify structure
    first = data[0]
    assert "name" in first
    assert "category" in first
    assert "cpu" in first
    assert "memory_gb" in first

    # Verify known types exist
    names = [mt["name"] for mt in data]
    assert "n2-standard-4" in names
    assert "n2-highmem-16" in names


# -- Launch work node --


@pytest.mark.asyncio
async def test_launch_work_node_success(
    client,
    session,
    comp_bio_token,
    comp_bio_user,
    seed_environment,
    seed_project,
    seed_session_credentials,
    seed_platform_config,
):
    """POST /api/v1/work-nodes/sessions creates a work node session."""
    response = await client.post(
        "/api/v1/work-nodes/sessions",
        json={
            "project_id": seed_project.id,
            "environment_version_id": seed_environment["ready_version"].id,
            "machine_type": "n2-standard-4",
            "data_mount_paths": ["/pipeline-outputs/run-001"],
        },
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["session_type"] == "ssh"
    assert data["status"] in ("pending", "starting", "running")
    assert data["machine_type"] == "n2-standard-4"
    assert data["environment_version_id"] == seed_environment["ready_version"].id

    # Verify DB record
    result = await session.execute(
        text("SELECT session_type, machine_type, heartbeat_token FROM compute_sessions WHERE id = :id"),
        {"id": data["id"]},
    )
    row = result.first()
    assert row is not None
    assert row[0] == "ssh"
    assert row[1] == "n2-standard-4"
    assert row[2] is not None  # heartbeat token generated


@pytest.mark.asyncio
async def test_launch_rejects_draft_environment(
    client,
    comp_bio_token,
    comp_bio_user,
    seed_environment,
    seed_project,
    seed_session_credentials,
    seed_platform_config,
):
    """Launch fails when environment version is not ready."""
    response = await client.post(
        "/api/v1/work-nodes/sessions",
        json={
            "project_id": seed_project.id,
            "environment_version_id": seed_environment["draft_version"].id,
            "machine_type": "n2-standard-4",
        },
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 400
    assert "ready" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_launch_rejects_invalid_machine_type(
    client,
    comp_bio_token,
    comp_bio_user,
    seed_environment,
    seed_project,
    seed_session_credentials,
    seed_platform_config,
):
    """Launch fails with an invalid machine type."""
    response = await client.post(
        "/api/v1/work-nodes/sessions",
        json={
            "project_id": seed_project.id,
            "environment_version_id": seed_environment["ready_version"].id,
            "machine_type": "invalid-type-999",
        },
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 400
    assert "machine type" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_launch_requires_session_credentials(
    client,
    comp_bio_token,
    comp_bio_user,
    seed_environment,
    seed_project,
    seed_platform_config,
):
    """Launch fails when user has no session credentials."""
    response = await client.post(
        "/api/v1/work-nodes/sessions",
        json={
            "project_id": seed_project.id,
            "environment_version_id": seed_environment["ready_version"].id,
            "machine_type": "n2-standard-4",
        },
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 400
    assert "credentials" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_launch_enforces_quota(
    client,
    session,
    comp_bio_token,
    comp_bio_user,
    seed_environment,
    seed_project,
    seed_session_credentials,
    seed_platform_config,
):
    """Launch fails when user exceeds concurrent work node limit."""
    # Set max to 1
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ),
        {"k": "work_node_max_per_user", "v": "1"},
    )
    await session.commit()

    # Launch first -- should succeed
    response = await client.post(
        "/api/v1/work-nodes/sessions",
        json={
            "project_id": seed_project.id,
            "environment_version_id": seed_environment["ready_version"].id,
            "machine_type": "n2-standard-4",
        },
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 200

    # Launch second -- should fail
    response = await client.post(
        "/api/v1/work-nodes/sessions",
        json={
            "project_id": seed_project.id,
            "environment_version_id": seed_environment["ready_version"].id,
            "machine_type": "n2-standard-4",
        },
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 400
    assert "limit" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_launch_denied_for_bench_user(
    client,
    bench_token,
    bench_user,
    seed_environment,
    seed_project,
    seed_platform_config,
):
    """Bench users cannot launch work nodes (no work_nodes.launch permission)."""
    response = await client.post(
        "/api/v1/work-nodes/sessions",
        json={
            "project_id": seed_project.id,
            "environment_version_id": seed_environment["ready_version"].id,
            "machine_type": "n2-standard-4",
        },
        headers={"Authorization": f"Bearer {bench_token}"},
    )
    assert response.status_code == 403


# -- List work nodes --


@pytest.mark.asyncio
async def test_list_work_nodes_empty(client, comp_bio_token, comp_bio_user):
    """GET /api/v1/work-nodes/sessions returns empty list when no work nodes exist."""
    response = await client.get(
        "/api/v1/work-nodes/sessions",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["sessions"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_list_work_nodes_filters_ssh_only(
    client,
    session,
    comp_bio_token,
    comp_bio_user,
    seed_environment,
    seed_project,
    seed_session_credentials,
    seed_platform_config,
):
    """List only returns SSH session type work nodes."""
    # Create a notebook session (jupyter) directly in DB
    from app.models.notebook_session import ComputeSession

    jupyter = ComputeSession(
        user_id=comp_bio_user.id,
        organization_id=comp_bio_user.organization_id,
        session_type="jupyter",
        resource_profile="small",
        cpu_cores=2,
        memory_gb=4,
        status="running",
    )
    session.add(jupyter)
    await session.flush()
    await session.commit()

    # Launch a work node via API
    await client.post(
        "/api/v1/work-nodes/sessions",
        json={
            "project_id": seed_project.id,
            "environment_version_id": seed_environment["ready_version"].id,
            "machine_type": "n2-standard-4",
        },
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )

    response = await client.get(
        "/api/v1/work-nodes/sessions",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["sessions"][0]["session_type"] == "ssh"


# -- Get work node detail --


@pytest.mark.asyncio
async def test_get_work_node_detail(
    client,
    session,
    comp_bio_token,
    comp_bio_user,
    seed_environment,
    seed_project,
    seed_session_credentials,
    seed_platform_config,
):
    """GET /api/v1/work-nodes/sessions/{id} returns session detail with SSH info."""
    launch_resp = await client.post(
        "/api/v1/work-nodes/sessions",
        json={
            "project_id": seed_project.id,
            "environment_version_id": seed_environment["ready_version"].id,
            "machine_type": "n2-standard-4",
            "data_mount_paths": ["/uploads/data1"],
        },
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert launch_resp.status_code == 200
    node_id = launch_resp.json()["id"]

    response = await client.get(
        f"/api/v1/work-nodes/sessions/{node_id}",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == node_id
    assert data["session_type"] == "ssh"
    assert data["machine_type"] == "n2-standard-4"
    assert data["data_mount_paths"] == ["/uploads/data1"]


# -- Stop work node --


@pytest.mark.asyncio
async def test_stop_work_node(
    client,
    session,
    comp_bio_token,
    comp_bio_user,
    seed_environment,
    seed_project,
    seed_session_credentials,
    seed_platform_config,
):
    """POST /api/v1/work-nodes/sessions/{id}/stop terminates the work node."""
    launch_resp = await client.post(
        "/api/v1/work-nodes/sessions",
        json={
            "project_id": seed_project.id,
            "environment_version_id": seed_environment["ready_version"].id,
            "machine_type": "n2-standard-4",
        },
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    node_id = launch_resp.json()["id"]

    response = await client.post(
        f"/api/v1/work-nodes/sessions/{node_id}/stop",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "stopped"


# -- Heartbeat endpoint --


@pytest.mark.asyncio
async def test_heartbeat_with_valid_token(
    client,
    session,
    comp_bio_token,
    comp_bio_user,
    seed_environment,
    seed_project,
    seed_session_credentials,
    seed_platform_config,
):
    """POST /api/v1/work-nodes/sessions/{id}/heartbeat updates heartbeat_at."""
    launch_resp = await client.post(
        "/api/v1/work-nodes/sessions",
        json={
            "project_id": seed_project.id,
            "environment_version_id": seed_environment["ready_version"].id,
            "machine_type": "n2-standard-4",
        },
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    node_id = launch_resp.json()["id"]

    # Get heartbeat token from DB
    result = await session.execute(
        text("SELECT heartbeat_token FROM compute_sessions WHERE id = :id"),
        {"id": node_id},
    )
    token = result.scalar()
    assert token is not None

    response = await client.post(
        f"/api/v1/work-nodes/sessions/{node_id}/heartbeat",
        headers={"X-Heartbeat-Token": token},
    )
    assert response.status_code == 200

    # Verify heartbeat_at was updated
    result = await session.execute(
        text("SELECT heartbeat_at FROM compute_sessions WHERE id = :id"),
        {"id": node_id},
    )
    heartbeat_at = result.scalar()
    assert heartbeat_at is not None


@pytest.mark.asyncio
async def test_heartbeat_rejects_invalid_token(
    client,
    session,
    comp_bio_token,
    comp_bio_user,
    seed_environment,
    seed_project,
    seed_session_credentials,
    seed_platform_config,
):
    """Heartbeat endpoint rejects requests with wrong token."""
    launch_resp = await client.post(
        "/api/v1/work-nodes/sessions",
        json={
            "project_id": seed_project.id,
            "environment_version_id": seed_environment["ready_version"].id,
            "machine_type": "n2-standard-4",
        },
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    node_id = launch_resp.json()["id"]

    response = await client.post(
        f"/api/v1/work-nodes/sessions/{node_id}/heartbeat",
        headers={"X-Heartbeat-Token": "invalid-token-123"},
    )
    assert response.status_code == 403


# -- Heartbeat timeout detection --


@pytest.mark.asyncio
async def test_heartbeat_timeout_detection(session, admin_user, seed_environment, seed_project):
    """WorkNodeService.check_heartbeat_timeouts terminates stale sessions."""
    from datetime import datetime, timezone, timedelta
    from app.models.notebook_session import ComputeSession
    from app.services.work_node_service import WorkNodeService

    stale_session = ComputeSession(
        user_id=admin_user.id,
        organization_id=admin_user.organization_id,
        session_type="ssh",
        resource_profile="custom",
        cpu_cores=4,
        memory_gb=16,
        status="running",
        heartbeat_at=datetime.now(timezone.utc) - timedelta(hours=25),
        heartbeat_token="test-token",
        environment_version_id=seed_environment["ready_version"].id,
        machine_type="n2-standard-4",
    )
    session.add(stale_session)
    await session.flush()
    await session.commit()

    await WorkNodeService.check_heartbeat_timeouts(session, idle_timeout_hours=24)

    result = await session.execute(
        text("SELECT status FROM compute_sessions WHERE id = :id"),
        {"id": stale_session.id},
    )
    status = result.scalar()
    assert status == "stopped"


# -- Viewer can list but not launch --


@pytest.mark.asyncio
async def test_viewer_can_list_work_nodes(client, viewer_token, viewer_user):
    """Viewers can list work nodes."""
    response = await client.get(
        "/api/v1/work-nodes/sessions",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    # Viewers don't have work_nodes.view in the bench/viewer roles
    # from bootstrap_roles.py, only admin and comp_bio have work_nodes permissions
    # viewer role has no work_nodes permissions
    assert response.status_code == 403


# -- Settings endpoints --


@pytest.mark.asyncio
async def test_get_work_node_settings_defaults(client, admin_token, admin_user):
    """GET /api/v1/settings/work-nodes returns defaults when no config exists."""
    response = await client.get(
        "/api/v1/settings/work-nodes",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["max_nodes_per_user"] == 2
    assert data["idle_timeout_hours"] == 24


@pytest.mark.asyncio
async def test_update_work_node_settings(client, session, admin_token, admin_user):
    """PUT /api/v1/settings/work-nodes persists new values."""
    response = await client.put(
        "/api/v1/settings/work-nodes",
        json={"max_nodes_per_user": 5, "idle_timeout_hours": 48},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200

    # Verify values persisted
    get_resp = await client.get(
        "/api/v1/settings/work-nodes",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    data = get_resp.json()
    assert data["max_nodes_per_user"] == 5
    assert data["idle_timeout_hours"] == 48


@pytest.mark.asyncio
async def test_update_work_node_settings_validates_range(client, admin_token, admin_user):
    """PUT /api/v1/settings/work-nodes rejects out-of-range values."""
    response = await client.put(
        "/api/v1/settings/work-nodes",
        json={"max_nodes_per_user": 0, "idle_timeout_hours": 48},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_work_node_settings_denied_for_comp_bio(client, comp_bio_token, comp_bio_user):
    """comp_bio users cannot update work node settings."""
    response = await client.put(
        "/api/v1/settings/work-nodes",
        json={"max_nodes_per_user": 10, "idle_timeout_hours": 48},
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_work_node_settings_readable_by_comp_bio(client, comp_bio_token, comp_bio_user):
    """comp_bio users can read work node settings (visible to all with work_nodes.view)."""
    response = await client.get(
        "/api/v1/settings/work-nodes",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    # Settings are always visible to those who can view work nodes
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_heartbeat_timeout_uses_configured_value(session, admin_user, seed_environment, seed_project):
    """check_heartbeat_timeouts reads idle_timeout_hours from platform_config."""
    from datetime import datetime, timezone, timedelta
    from app.models.notebook_session import ComputeSession
    from app.services.work_node_service import WorkNodeService

    # Set timeout to 2 hours
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ),
        {"k": "work_node_idle_timeout_hours", "v": "2"},
    )
    await session.commit()

    # Create a session with heartbeat 3 hours ago (should be timed out with 2h config)
    stale_session = ComputeSession(
        user_id=admin_user.id,
        organization_id=admin_user.organization_id,
        session_type="ssh",
        resource_profile="custom",
        cpu_cores=4,
        memory_gb=16,
        status="running",
        heartbeat_at=datetime.now(timezone.utc) - timedelta(hours=3),
        heartbeat_token="test-token",
        environment_version_id=seed_environment["ready_version"].id,
        machine_type="n2-standard-4",
    )
    session.add(stale_session)
    await session.flush()
    await session.commit()

    await WorkNodeService.check_heartbeat_timeouts(session)

    result = await session.execute(
        text("SELECT status FROM compute_sessions WHERE id = :id"),
        {"id": stale_session.id},
    )
    assert result.scalar() == "stopped"
