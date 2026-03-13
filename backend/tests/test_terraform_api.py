"""Tests for /api/v1/infrastructure/terraform/ endpoints (Step 9 - Phase 17).

Tests 17-25 from the spec:
- 17: Only admins can call terraform endpoints (403 for non-admin)
- 18: Plan fails with 409 if GCP not configured
- 19: Plan returns plan summary JSON
- 20: Apply streams SSE events (text/event-stream)
- 21: Bootstrap streams SSE events
- 22: Status endpoint returns terraform status
- 23: Run detail endpoint returns run data
- 24: Bootstrap 409 when already initialized
- 25: Plan 409 when run already in progress
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text

from app.services.terraform_executor import TerraformExecutor, TerraformProgressEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_user_and_token(client, session, role="admin"):
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.auth_service import AuthService

    org = Organization(name="ApiTestOrg", setup_complete=True)
    session.add(org)
    await session.flush()

    user = User(
        email=f"{role}_api@test.com",
        password_hash=AuthService.hash_password("pw"),
        role=role,
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()

    token = AuthService.create_token(user.id, user.email, user.role, user.organization_id)
    return user, token


async def _seed_gcp_config(session, configured=True, initialized=False):
    rows = [
        ("gcp_credentials_configured", "true" if configured else "false"),
        ("gcp_credential_source", "vm_default"),
        ("gcp_project_id", "test-project"),
        ("gcp_region", "us-central1"),
        ("gcp_zone", "us-central1-a"),
        ("terraform_initialized", "true" if initialized else "false"),
        ("terraform_state_bucket", "bioaf-tfstate-test" if initialized else ""),
    ]
    for key, value in rows:
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=key, v=value)
        )
    await session.commit()


# ---------------------------------------------------------------------------
# Test 17: Auth - non-admin gets 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terraform_status_requires_admin(client, session):
    """Non-admin user gets 403 on terraform status endpoint."""
    _, viewer_token = await _seed_user_and_token(client, session, role="viewer")
    await _seed_gcp_config(session)

    resp = await client.get(
        "/api/v1/infrastructure/terraform/status",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_terraform_plan_requires_admin(client, session):
    """Non-admin user gets 403 on terraform plan endpoint."""
    _, viewer_token = await _seed_user_and_token(client, session, role="viewer")

    resp = await client.post(
        "/api/v1/infrastructure/terraform/plan",
        json={"module_name": "foundation"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Test 18: Plan pre-condition - GCP not configured
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terraform_plan_409_when_gcp_not_configured(client, session):
    """Plan returns 409 if GCP credentials are not configured."""
    _, admin_token = await _seed_user_and_token(client, session)
    await _seed_gcp_config(session, configured=False)

    with patch.object(TerraformExecutor, "run_plan", new=AsyncMock(side_effect=ValueError("GCP not configured"))):
        resp = await client.post(
            "/api/v1/infrastructure/terraform/plan",
            json={"module_name": "foundation"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Test 19: Plan returns result JSON
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terraform_plan_returns_json(client, session):
    """Successful plan returns JSON with plan summary."""
    _, admin_token = await _seed_user_and_token(client, session)
    await _seed_gcp_config(session, configured=True, initialized=True)

    mock_run = MagicMock()
    mock_run.id = 1
    mock_run.action = "plan"
    mock_run.module_name = "foundation"
    mock_run.status = "awaiting_confirmation"
    mock_run.resources_planned = 1
    mock_run.plan_json = {"add_count": 1, "change_count": 0, "destroy_count": 0, "total": 1, "resources": []}
    mock_run.plan_summary_json = None
    mock_run.component_key = None
    mock_run.triggered_by_user_id = 1
    mock_run.started_at = "2026-03-11T00:00:00Z"
    mock_run.completed_at = None
    mock_run.error_message = None
    mock_run.terraform_state_url = None
    mock_run.resources_completed = 0

    with patch.object(TerraformExecutor, "run_plan", new=AsyncMock(return_value=mock_run)):
        resp = await client.post(
            "/api/v1/infrastructure/terraform/plan",
            json={"module_name": "foundation"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "awaiting_confirmation"
    assert body["resources_planned"] == 1


# ---------------------------------------------------------------------------
# Test 20: Apply streams SSE events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terraform_apply_streams_sse(client, session):
    """Apply endpoint returns text/event-stream response."""
    user, admin_token = await _seed_user_and_token(client, session)
    await _seed_gcp_config(session, configured=True, initialized=True)

    # Create a dummy run in awaiting_confirmation state
    await session.execute(
        text("""
        INSERT INTO terraform_runs (triggered_by_user_id, action, status, module_name, resources_planned)
        VALUES (:uid, 'plan', 'awaiting_confirmation', 'foundation', 1)
        """).bindparams(uid=user.id)
    )
    await session.commit()
    run_row = (await session.execute(text("SELECT id FROM terraform_runs LIMIT 1"))).fetchone()
    run_id = run_row[0]

    async def mock_apply_gen(*args, **kwargs):
        yield TerraformProgressEvent(
            event_type="resource_complete",
            message="Applied bucket",
            resources_completed=1,
            resources_total=1,
        )
        yield TerraformProgressEvent(
            event_type="apply_complete",
            message="Done",
            resources_completed=1,
            resources_total=1,
        )

    with patch.object(TerraformExecutor, "run_apply", new=mock_apply_gen):
        resp = await client.post(
            f"/api/v1/infrastructure/terraform/apply/{run_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# Test 21: Bootstrap streams SSE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terraform_bootstrap_streams_sse(client, session):
    """Bootstrap endpoint returns text/event-stream response."""
    _, admin_token = await _seed_user_and_token(client, session)
    await _seed_gcp_config(session, configured=True, initialized=False)

    async def mock_bootstrap_gen(*args, **kwargs):
        yield TerraformProgressEvent(event_type="progress", message="Starting...")
        yield TerraformProgressEvent(event_type="apply_complete", message="Done")

    with patch.object(TerraformExecutor, "bootstrap_foundation", new=mock_bootstrap_gen):
        resp = await client.post(
            "/api/v1/infrastructure/terraform/bootstrap",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# Test 22: Status endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terraform_status_endpoint(client, session):
    """Status endpoint returns terraform_initialized and other config values."""
    _, admin_token = await _seed_user_and_token(client, session)
    await _seed_gcp_config(session, configured=True, initialized=False)

    resp = await client.get(
        "/api/v1/infrastructure/terraform/status",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "terraform_initialized" in body
    assert body["terraform_initialized"] is False


# ---------------------------------------------------------------------------
# Test 23: Run detail endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terraform_run_detail_endpoint(client, session):
    """Run detail endpoint returns run data by ID."""
    user, admin_token = await _seed_user_and_token(client, session)
    await _seed_gcp_config(session, configured=True, initialized=True)

    await session.execute(
        text("""
        INSERT INTO terraform_runs (triggered_by_user_id, action, status, module_name)
        VALUES (:uid, 'plan', 'completed', 'foundation')
        """).bindparams(uid=user.id)
    )
    await session.commit()
    run_row = (await session.execute(text("SELECT id FROM terraform_runs LIMIT 1"))).fetchone()
    run_id = run_row[0]

    resp = await client.get(
        f"/api/v1/infrastructure/terraform/runs/{run_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == run_id
    assert body["action"] == "plan"


# ---------------------------------------------------------------------------
# Test 24: Bootstrap 409 when already initialized
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terraform_bootstrap_commits_state(client, session):
    """Bootstrap endpoint commits terraform_initialized to DB so it persists after stream ends."""
    user, admin_token = await _seed_user_and_token(client, session)
    await _seed_gcp_config(session, configured=True, initialized=False)

    async def mock_bootstrap_gen(*args, **kwargs):
        # Simulate the service updating platform_config (as the real service does)
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k="terraform_initialized", v="true")
        )
        await session.flush()
        yield TerraformProgressEvent(event_type="progress", message="Starting...")
        yield TerraformProgressEvent(event_type="apply_complete", message="Done")

    with patch.object(TerraformExecutor, "bootstrap_foundation", new=mock_bootstrap_gen):
        resp = await client.post(
            "/api/v1/infrastructure/terraform/bootstrap",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 200

    # Verify the state was committed by reading it in a fresh query
    row = (
        await session.execute(text("SELECT value FROM platform_config WHERE key = 'terraform_initialized'"))
    ).scalar()
    assert row == "true"


@pytest.mark.asyncio
async def test_terraform_apply_commits_state(client, session):
    """Apply endpoint commits run status to DB after stream completes."""
    user, admin_token = await _seed_user_and_token(client, session)
    await _seed_gcp_config(session, configured=True, initialized=True)

    # Create a run in awaiting_confirmation state
    await session.execute(
        text("""
        INSERT INTO terraform_runs (triggered_by_user_id, action, status, module_name, resources_planned)
        VALUES (:uid, 'plan', 'awaiting_confirmation', 'foundation', 1)
        """).bindparams(uid=user.id)
    )
    await session.commit()
    run_row = (await session.execute(text("SELECT id FROM terraform_runs LIMIT 1"))).fetchone()
    run_id = run_row[0]

    async def mock_apply_gen(*args, **kwargs):
        # Simulate the service updating the run status
        await session.execute(
            text("UPDATE terraform_runs SET status = 'completed' WHERE id = :rid").bindparams(rid=run_id)
        )
        await session.flush()
        yield TerraformProgressEvent(
            event_type="apply_complete",
            message="Done",
            resources_completed=1,
            resources_total=1,
        )

    with patch.object(TerraformExecutor, "run_apply", new=mock_apply_gen):
        resp = await client.post(
            f"/api/v1/infrastructure/terraform/apply/{run_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 200

    # Verify run status was committed
    status = (
        await session.execute(text("SELECT status FROM terraform_runs WHERE id = :rid").bindparams(rid=run_id))
    ).scalar()
    assert status == "completed"


@pytest.mark.asyncio
async def test_terraform_bootstrap_409_when_initialized(client, session):
    """Bootstrap returns 409 if already initialized."""
    _, admin_token = await _seed_user_and_token(client, session)
    await _seed_gcp_config(session, configured=True, initialized=True)

    async def mock_raises(*args, **kwargs):
        raise ValueError("already initialized")
        yield  # make it an async generator

    with patch.object(TerraformExecutor, "bootstrap_foundation", new=mock_raises):
        resp = await client.post(
            "/api/v1/infrastructure/terraform/bootstrap",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Test 25: Plan 409 when run in progress
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terraform_plan_409_when_run_in_progress(client, session):
    """Plan returns 409 if another run is in progress."""
    _, admin_token = await _seed_user_and_token(client, session)
    await _seed_gcp_config(session, configured=True, initialized=True)

    with patch.object(TerraformExecutor, "run_plan", new=AsyncMock(side_effect=ValueError("in progress"))):
        resp = await client.post(
            "/api/v1/infrastructure/terraform/plan",
            json={"module_name": "foundation"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert resp.status_code == 409
