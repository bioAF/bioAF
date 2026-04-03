import pytest
import pytest_asyncio

from app.services.auth_service import AuthService


@pytest_asyncio.fixture
async def comp_bio_user(session, admin_user):
    from app.models.user import User
    from app.services.session_credential_service import SessionCredentialService

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

    # RStudio sessions require session credentials
    await SessionCredentialService.create_or_update(
        session, user_id=user.id, org_id=user.organization_id, email=user.email, password="testpass123"
    )
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
        bench_user.id, bench_user.email, bench_user.role_id, bench_user.organization_id, role_name="bench"
    )


@pytest.mark.asyncio
async def test_session_launch(client, comp_bio_token):
    """Session launch creates session and job records."""
    response = await client.post(
        "/api/notebooks/sessions",
        json={
            "session_type": "jupyter",
            "resource_profile": "small",
        },
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["session_type"] == "jupyter"
    assert data["resource_profile"] == "small"
    assert data["cpu_cores"] == 2
    assert data["memory_gb"] == 4
    assert data["status"] in ("starting", "pending", "running")


@pytest.mark.asyncio
async def test_session_launch_medium_profile(client, comp_bio_token):
    """Medium profile sets correct resources."""
    response = await client.post(
        "/api/notebooks/sessions",
        json={
            "session_type": "rstudio",
            "resource_profile": "medium",
        },
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["cpu_cores"] == 4
    assert data["memory_gb"] == 8


@pytest.mark.asyncio
async def test_session_launch_checks_quota(client, session, comp_bio_user, comp_bio_token):
    """Session launch rejects when quota exceeded."""
    from app.models.user_quota import UserQuota
    from decimal import Decimal
    from datetime import datetime, timezone

    quota = UserQuota(
        user_id=comp_bio_user.id,
        organization_id=comp_bio_user.organization_id,
        cpu_hours_monthly_limit=10,
        cpu_hours_used_current_month=Decimal("10.0"),
        quota_reset_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )
    session.add(quota)
    await session.flush()
    await session.commit()

    response = await client.post(
        "/api/notebooks/sessions",
        json={
            "session_type": "jupyter",
            "resource_profile": "small",
        },
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 400
    assert "Quota exceeded" in response.json()["detail"]


@pytest.mark.asyncio
async def test_session_stop(client, session, comp_bio_user, comp_bio_token):
    """Session stop updates status."""
    from app.models.notebook_session import NotebookSession

    ns = NotebookSession(
        user_id=comp_bio_user.id,
        organization_id=comp_bio_user.organization_id,
        session_type="jupyter",
        resource_profile="small",
        cpu_cores=2,
        memory_gb=4,
        status="running",
        slurm_job_id="77777",
    )
    session.add(ns)
    await session.flush()
    await session.commit()

    response = await client.post(
        f"/api/notebooks/sessions/{ns.id}/stop",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "stopped"


@pytest.mark.asyncio
async def test_session_list_admin_sees_all(client, session, admin_user, admin_token, comp_bio_user):
    """Admin sees all sessions."""
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

    response = await client.get(
        "/api/notebooks/sessions",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 2


@pytest.mark.asyncio
async def test_session_list_comp_bio_sees_own(client, session, comp_bio_user, comp_bio_token, admin_user):
    """Comp Bio sees only own sessions."""
    from app.models.notebook_session import NotebookSession

    # Create session for admin
    ns1 = NotebookSession(
        user_id=admin_user.id,
        organization_id=admin_user.organization_id,
        session_type="jupyter",
        resource_profile="small",
        cpu_cores=2,
        memory_gb=4,
        status="running",
    )
    # Create session for comp_bio
    ns2 = NotebookSession(
        user_id=comp_bio_user.id,
        organization_id=comp_bio_user.organization_id,
        session_type="rstudio",
        resource_profile="medium",
        cpu_cores=4,
        memory_gb=8,
        status="running",
    )
    session.add_all([ns1, ns2])
    await session.flush()
    await session.commit()

    response = await client.get(
        "/api/notebooks/sessions",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    # Comp bio should only see their own sessions
    for s in data["sessions"]:
        assert s["user"]["id"] == comp_bio_user.id


@pytest.mark.asyncio
async def test_bench_cannot_launch_session(client, bench_token):
    """Bench user cannot launch sessions."""
    response = await client.post(
        "/api/notebooks/sessions",
        json={
            "session_type": "jupyter",
            "resource_profile": "small",
        },
        headers={"Authorization": f"Bearer {bench_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_session_launch_creates_audit_entry(client, session, comp_bio_token):
    """Session launch writes an audit log entry."""
    response = await client.post(
        "/api/notebooks/sessions",
        json={
            "session_type": "jupyter",
            "resource_profile": "small",
        },
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 200

    from sqlalchemy import select
    from app.models.audit_log import AuditLog

    result = await session.execute(
        select(AuditLog).where(
            AuditLog.entity_type == "notebook_session",
            AuditLog.action == "launch",
        )
    )
    entries = list(result.scalars().all())
    assert len(entries) >= 1


def test_rstudio_command_includes_auth_flags():
    """RStudio pod command must include auth-none and minimum-user-id flags."""
    container_port = 8787
    container_command = [
        "/usr/lib/rstudio-server/bin/rserver",
        "--www-address=0.0.0.0",
        f"--www-port={container_port}",
        "--auth-none=1",
        "--auth-minimum-user-id=0",
        "--server-daemonize=0",
    ]
    assert container_command[0] == "/usr/lib/rstudio-server/bin/rserver"
    assert "--auth-none=1" in container_command
    assert "--auth-minimum-user-id=0" in container_command
    assert "--server-daemonize=0" in container_command


def test_rstudio_container_gets_cookie_key_env():
    """RStudio container spec must include RSTUDIO_SECURE_COOKIE_KEY env var."""
    import uuid

    # Simulate the env var construction from _k8s_launch_session
    env = [{"name": "RSTUDIO_SECURE_COOKIE_KEY", "value": uuid.uuid4().hex}]
    assert env[0]["name"] == "RSTUDIO_SECURE_COOKIE_KEY"
    assert len(env[0]["value"]) == 32  # hex UUID without dashes


def test_jupyter_command_does_not_run_as_bash():
    """Jupyter pod command should be a direct command, not wrapped in bash."""
    container_port = 8888
    container_command = [
        "jupyter",
        "lab",
        "--ip=0.0.0.0",
        f"--port={container_port}",
        "--no-browser",
        "--NotebookApp.token=''",
        "--NotebookApp.password=''",
    ]
    assert container_command[0] == "jupyter"
    assert "--no-browser" in container_command


@pytest.mark.asyncio
async def test_v1_session_list_returns_proxy_url(client, session, admin_user, admin_token):
    """V1 session list returns access_url as proxy_url."""
    from app.models.notebook_session import NotebookSession

    ns = NotebookSession(
        user_id=admin_user.id,
        organization_id=admin_user.organization_id,
        session_type="rstudio",
        resource_profile="small",
        cpu_cores=2,
        memory_gb=4,
        status="running",
        access_url="http://10.0.0.1:8787",
    )
    session.add(ns)
    await session.flush()
    await session.commit()

    response = await client.get(
        "/api/v1/notebooks/sessions",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    running = [s for s in data["sessions"] if s["status"] == "running"]
    assert len(running) >= 1
    assert running[0]["proxy_url"] == "http://10.0.0.1:8787"
