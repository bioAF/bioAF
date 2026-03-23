"""Tests for the idle session monitor background task (tests 16-19 from spec)."""

import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock

from app.services.auth_service import AuthService


@pytest_asyncio.fixture
async def comp_bio_user(session, admin_user):
    from app.models.user import User

    password_hash = AuthService.hash_password("compbiopass123")
    user = User(
        email="compbio-monitor@test.com",
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
async def running_session(session, comp_bio_user):
    from app.models.notebook_session import NotebookSession

    ns = NotebookSession(
        user_id=comp_bio_user.id,
        organization_id=comp_bio_user.organization_id,
        session_type="jupyter",
        resource_profile="small",
        cpu_cores=2,
        memory_gb=4,
        status="running",
        k8s_pod_name="bioaf-notebook-99",
        k8s_namespace="bioaf-notebooks",
        gcs_home_prefix=f"gs://bioaf-working/notebooks/{comp_bio_user.id}/",
        started_at=datetime.now(timezone.utc) - timedelta(hours=6),
        last_activity_at=datetime.now(timezone.utc) - timedelta(hours=5),
    )
    session.add(ns)
    await session.flush()
    await session.commit()
    return ns


@pytest.mark.asyncio
async def test_monitor_detects_idle_session(session, running_session):
    """Test 16: session with last_activity_at older than timeout is terminated."""
    from app.services.session_monitor import SessionMonitorService

    with patch.object(SessionMonitorService, "_terminate_idle_session", new_callable=AsyncMock) as mock_terminate:
        await SessionMonitorService.poll_notebook_sessions(session, idle_timeout_hours=4)
        mock_terminate.assert_called_once()


@pytest.mark.asyncio
async def test_monitor_sends_warning_before_shutdown(session, comp_bio_user):
    """Test 17: session within warning window gets warning, not termination."""
    from app.models.notebook_session import NotebookSession
    from app.services.session_monitor import SessionMonitorService

    # Session idle for 3h 50m (within 15-min warning of 4h timeout)
    ns = NotebookSession(
        user_id=comp_bio_user.id,
        organization_id=comp_bio_user.organization_id,
        session_type="jupyter",
        resource_profile="small",
        cpu_cores=2,
        memory_gb=4,
        status="running",
        k8s_pod_name="bioaf-notebook-100",
        k8s_namespace="bioaf-notebooks",
        started_at=datetime.now(timezone.utc) - timedelta(hours=4),
        last_activity_at=datetime.now(timezone.utc) - timedelta(hours=3, minutes=50),
    )
    session.add(ns)
    await session.flush()
    await session.commit()

    with (
        patch.object(SessionMonitorService, "_terminate_idle_session", new_callable=AsyncMock) as mock_terminate,
        patch.object(SessionMonitorService, "_send_idle_warning", new_callable=AsyncMock) as mock_warn,
    ):
        await SessionMonitorService.poll_notebook_sessions(session, idle_timeout_hours=4, warning_minutes=15)
        mock_warn.assert_called_once()
        mock_terminate.assert_not_called()


@pytest.mark.asyncio
async def test_monitor_ignores_active_session(session, comp_bio_user):
    """Test 18: session with recent activity is not terminated."""
    from app.models.notebook_session import NotebookSession
    from app.services.session_monitor import SessionMonitorService

    ns = NotebookSession(
        user_id=comp_bio_user.id,
        organization_id=comp_bio_user.organization_id,
        session_type="jupyter",
        resource_profile="small",
        cpu_cores=2,
        memory_gb=4,
        status="running",
        k8s_pod_name="bioaf-notebook-101",
        k8s_namespace="bioaf-notebooks",
        started_at=datetime.now(timezone.utc) - timedelta(hours=1),
        last_activity_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    session.add(ns)
    await session.flush()
    await session.commit()

    with (
        patch.object(SessionMonitorService, "_terminate_idle_session", new_callable=AsyncMock) as mock_terminate,
        patch.object(SessionMonitorService, "_send_idle_warning", new_callable=AsyncMock) as mock_warn,
    ):
        await SessionMonitorService.poll_notebook_sessions(session, idle_timeout_hours=4)
        mock_terminate.assert_not_called()
        mock_warn.assert_not_called()


@pytest.mark.asyncio
async def test_monitor_handles_missing_metrics(session, comp_bio_user):
    """Test 19: when last_activity_at is None, falls back to started_at for idle check."""
    from app.models.notebook_session import NotebookSession
    from app.services.session_monitor import SessionMonitorService

    ns = NotebookSession(
        user_id=comp_bio_user.id,
        organization_id=comp_bio_user.organization_id,
        session_type="jupyter",
        resource_profile="small",
        cpu_cores=2,
        memory_gb=4,
        status="running",
        k8s_pod_name="bioaf-notebook-102",
        k8s_namespace="bioaf-notebooks",
        started_at=datetime.now(timezone.utc) - timedelta(hours=6),
        last_activity_at=None,
    )
    session.add(ns)
    await session.flush()
    await session.commit()

    with patch.object(SessionMonitorService, "_terminate_idle_session", new_callable=AsyncMock) as mock_terminate:
        await SessionMonitorService.poll_notebook_sessions(session, idle_timeout_hours=4)
        mock_terminate.assert_called_once()
