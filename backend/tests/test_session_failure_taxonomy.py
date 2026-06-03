"""Backend contract for the Notebook + Work Node UX upgrade.

Covers:
1. Migration 094 adds the failure taxonomy columns and the requested-disk
   column to compute_sessions.
2. SessionResponse (notebook) and WorkNodeResponse expose `failure_reason`,
   `failure_message`, `requested_disk_gb`, and `project` (ProjectSummary)
   so the UI can render "Resource Failure" + disk size + Linked-to column.
3. The list endpoints accept `bucket=active|recent|all`:
     - active: status NOT IN ('stopped', 'failed')
     - recent: created_at > now() - 24h, any status
     - all:    no filter (default behavior preserved if absent)
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.services.auth_service import AuthService


MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION_FILE = MIGRATIONS_DIR / "094_session_failure_taxonomy.py"


# ----- migration file shape -----


def test_migration_file_exists():
    assert MIGRATION_FILE.exists(), (
        f"Expected migration file at {MIGRATION_FILE}. "
        "It should add failure_reason, failure_message, requested_disk_gb to compute_sessions."
    )


def test_migration_chains_to_093():
    content = MIGRATION_FILE.read_text()
    assert 'revision = "094"' in content
    assert 'down_revision = "093"' in content, (
        "migration 094 must chain to 093 so alembic upgrade head picks it up"
    )


def test_migration_adds_failure_taxonomy_columns():
    content = MIGRATION_FILE.read_text()
    assert "failure_reason" in content, "upgrade() must add failure_reason column"
    assert "failure_message" in content, "upgrade() must add failure_message column"
    assert "requested_disk_gb" in content, "upgrade() must add requested_disk_gb column"
    # All three are nullable so the migration is non-blocking for existing rows.
    for col in ("failure_reason", "failure_message", "requested_disk_gb"):
        assert (
            f"'{col}'" in content or f'"{col}"' in content
        ), f"migration must reference column {col} by name"


def test_migration_downgrade_drops_the_columns():
    content = MIGRATION_FILE.read_text()
    assert "drop_column" in content, "downgrade() must drop the columns it added"


# ----- response schema shape -----


def test_session_response_includes_failure_and_disk_fields():
    from app.schemas.notebook_session import SessionResponse

    fields = SessionResponse.model_fields
    assert "failure_reason" in fields, (
        "SessionResponse must expose failure_reason so the UI can render 'Resource Failure'"
    )
    assert "failure_message" in fields, "SessionResponse must expose failure_message for the detail modal"
    assert "requested_disk_gb" in fields, "SessionResponse must expose requested_disk_gb for the detail modal"
    assert "project" in fields, "SessionResponse must expose project (for the Linked-to column)"


def test_work_node_response_includes_failure_and_disk_fields():
    from app.schemas.work_node import WorkNodeResponse

    fields = WorkNodeResponse.model_fields
    assert "failure_reason" in fields
    assert "failure_message" in fields
    assert "requested_disk_gb" in fields
    assert "project" in fields, (
        "WorkNodeResponse already has project_id; project (ProjectSummary) lets the UI render the project name"
    )


# ----- bucket filter on the list endpoints -----


@pytest_asyncio.fixture
async def comp_bio_user(session, admin_user):
    from app.models.user import User

    user = User(
        email="bucket-filter@test.com",
        password_hash=AuthService.hash_password("buckpass123"),
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


async def _seed_notebook(session, *, user_id, org_id, status, created_at=None):
    from app.models.notebook_session import NotebookSession

    ns = NotebookSession(
        user_id=user_id,
        organization_id=org_id,
        session_type="jupyter",
        resource_profile="small",
        cpu_cores=2,
        memory_gb=8,
        status=status,
    )
    if created_at is not None:
        ns.created_at = created_at
    session.add(ns)
    await session.flush()
    return ns


async def _seed_work_node(session, *, user_id, org_id, status, created_at=None):
    from app.models.notebook_session import ComputeSession

    cs = ComputeSession(
        user_id=user_id,
        organization_id=org_id,
        session_type="ssh",
        resource_profile="custom",
        machine_type="e2-standard-8",
        cpu_cores=8,
        memory_gb=32,
        status=status,
    )
    if created_at is not None:
        cs.created_at = created_at
    session.add(cs)
    await session.flush()
    return cs


@pytest.mark.asyncio
async def test_notebook_bucket_active_excludes_terminal_states(
    client, session, comp_bio_user, comp_bio_token
):
    """bucket=active returns sessions whose status is not stopped/failed."""
    org_id = comp_bio_user.organization_id
    for status in ("starting", "running", "stopped", "failed"):
        await _seed_notebook(session, user_id=comp_bio_user.id, org_id=org_id, status=status)
    await session.commit()

    resp = await client.get(
        "/api/v1/notebooks/sessions?bucket=active",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert resp.status_code == 200
    statuses = {s["status"] for s in resp.json()["sessions"]}
    assert "stopped" not in statuses
    assert "failed" not in statuses
    assert {"starting", "running"} <= statuses


@pytest.mark.asyncio
async def test_notebook_bucket_recent_returns_last_24h_regardless_of_status(
    client, session, comp_bio_user, comp_bio_token
):
    """bucket=recent returns rows created in the last 24h, including failed/stopped."""
    org_id = comp_bio_user.organization_id
    now = datetime.now(timezone.utc)
    fresh_failed = await _seed_notebook(
        session, user_id=comp_bio_user.id, org_id=org_id, status="failed", created_at=now - timedelta(hours=2)
    )
    fresh_stopped = await _seed_notebook(
        session, user_id=comp_bio_user.id, org_id=org_id, status="stopped", created_at=now - timedelta(hours=10)
    )
    stale_running = await _seed_notebook(
        session, user_id=comp_bio_user.id, org_id=org_id, status="running", created_at=now - timedelta(days=3)
    )
    await session.commit()

    resp = await client.get(
        "/api/v1/notebooks/sessions?bucket=recent",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert resp.status_code == 200
    ids = {s["id"] for s in resp.json()["sessions"]}
    assert fresh_failed.id in ids
    assert fresh_stopped.id in ids
    assert stale_running.id not in ids, (
        "stale 3-day-old session must not appear under bucket=recent even if it is running"
    )


@pytest.mark.asyncio
async def test_notebook_bucket_all_returns_everything(
    client, session, comp_bio_user, comp_bio_token
):
    org_id = comp_bio_user.organization_id
    for status in ("running", "stopped", "failed"):
        await _seed_notebook(session, user_id=comp_bio_user.id, org_id=org_id, status=status)
    await session.commit()

    resp = await client.get(
        "/api/v1/notebooks/sessions?bucket=all",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert resp.status_code == 200
    statuses = {s["status"] for s in resp.json()["sessions"]}
    assert {"running", "stopped", "failed"} <= statuses


@pytest.mark.asyncio
async def test_work_node_bucket_active_excludes_terminal_states(
    client, session, comp_bio_user, comp_bio_token
):
    org_id = comp_bio_user.organization_id
    for status in ("starting", "running", "stopped", "failed"):
        await _seed_work_node(session, user_id=comp_bio_user.id, org_id=org_id, status=status)
    await session.commit()

    resp = await client.get(
        "/api/v1/work-nodes/sessions?bucket=active",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert resp.status_code == 200
    statuses = {s["status"] for s in resp.json()["sessions"]}
    assert "stopped" not in statuses
    assert "failed" not in statuses
    assert {"starting", "running"} <= statuses


@pytest.mark.asyncio
async def test_work_node_bucket_recent_returns_last_24h_regardless_of_status(
    client, session, comp_bio_user, comp_bio_token
):
    org_id = comp_bio_user.organization_id
    now = datetime.now(timezone.utc)
    fresh_failed = await _seed_work_node(
        session, user_id=comp_bio_user.id, org_id=org_id, status="failed", created_at=now - timedelta(hours=2)
    )
    stale_running = await _seed_work_node(
        session, user_id=comp_bio_user.id, org_id=org_id, status="running", created_at=now - timedelta(days=3)
    )
    await session.commit()

    resp = await client.get(
        "/api/v1/work-nodes/sessions?bucket=recent",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert resp.status_code == 200
    ids = {s["id"] for s in resp.json()["sessions"]}
    assert fresh_failed.id in ids
    assert stale_running.id not in ids


@pytest.mark.asyncio
async def test_bucket_invalid_value_rejected(client, comp_bio_token):
    resp = await client.get(
        "/api/v1/notebooks/sessions?bucket=garbage",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert resp.status_code in (400, 422), "Unknown bucket must be rejected, not silently treated as 'all'"


@pytest.mark.asyncio
async def test_failure_reason_and_disk_round_trip_in_list_response(
    client, session, comp_bio_user, comp_bio_token
):
    """A session with failure_reason + failure_message + requested_disk_gb populated
    must surface those fields in the list response (notebook + work-node)."""
    from app.models.notebook_session import NotebookSession, ComputeSession

    ns = NotebookSession(
        user_id=comp_bio_user.id,
        organization_id=comp_bio_user.organization_id,
        session_type="jupyter",
        resource_profile="small",
        cpu_cores=2,
        memory_gb=8,
        status="failed",
        failure_reason="resource_exhausted",
        failure_message="GCE out of resources in us-central1-a for e2-standard-8.",
        requested_disk_gb=100,
    )
    session.add(ns)

    wn = ComputeSession(
        user_id=comp_bio_user.id,
        organization_id=comp_bio_user.organization_id,
        session_type="ssh",
        resource_profile="custom",
        machine_type="e2-standard-8",
        cpu_cores=8,
        memory_gb=32,
        status="failed",
        failure_reason="resource_exhausted",
        failure_message="GCE out of resources in us-central1-a for e2-standard-8.",
        requested_disk_gb=100,
    )
    session.add(wn)
    await session.flush()
    await session.commit()

    nb_resp = await client.get(
        "/api/v1/notebooks/sessions?bucket=all",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert nb_resp.status_code == 200
    matching = [s for s in nb_resp.json()["sessions"] if s["id"] == ns.id]
    assert matching, "newly-seeded notebook session must appear in /sessions list"
    body = matching[0]
    assert body["failure_reason"] == "resource_exhausted"
    assert "GCE out of resources" in (body["failure_message"] or "")
    assert body["requested_disk_gb"] == 100

    wn_resp = await client.get(
        "/api/v1/work-nodes/sessions?bucket=all",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert wn_resp.status_code == 200
    matching = [s for s in wn_resp.json()["sessions"] if s["id"] == wn.id]
    assert matching, "newly-seeded work node must appear in /sessions list"
    body = matching[0]
    assert body["failure_reason"] == "resource_exhausted"
    assert "GCE out of resources" in (body["failure_message"] or "")
    assert body["requested_disk_gb"] == 100
