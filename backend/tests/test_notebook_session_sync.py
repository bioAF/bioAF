"""_sync_session_from_k8s routes through the NotebookProvider interface.

BAL rework, Phase 5: the notebook-session list endpoint used to reach into
adapter privates (_get_k8s_core_client / _get_api_client / is_local) and build
K8s service URLs by hand. It now reconciles the DB record from the normalized
SessionStatus returned by get_session_status().
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.adapters.models import ServiceState, SessionStatus
from app.api.notebook_sessions import _sync_session_from_k8s
from app.models.notebook_session import NotebookSession


async def _ns(session, admin_user, **overrides):
    """A real session row; the list endpoint syncs ORM rows it loaded in the request session."""
    fields = dict(
        user_id=admin_user.id,
        organization_id=admin_user.organization_id,
        session_type="jupyter",
        resource_profile="small",
        cpu_cores=2,
        memory_gb=8,
        status="starting",
        access_url=None,
        started_at=None,
    )
    job_ref = overrides.pop("compute_job_ref", "auto")
    fields.update(overrides)
    ns = NotebookSession(**fields)
    session.add(ns)
    await session.flush()
    ns.compute_job_ref = f"bioaf-notebook-{ns.id}" if job_ref == "auto" else job_ref
    ns.provider_metadata = {"namespace": "bioaf-notebooks"} if ns.compute_job_ref else None
    await session.commit()
    return ns


def _adapter(status: SessionStatus):
    adapter = MagicMock()
    adapter.get_session_status = AsyncMock(return_value=status)
    return adapter


async def _sync(ns, session, status):
    adapter = _adapter(status)
    with patch("app.services.notebook_lifecycle.get_notebook_adapter", return_value=adapter):
        await _sync_session_from_k8s(ns, session)
    await session.commit()
    return adapter


async def _stored(session, ns):
    await session.refresh(ns)
    return ns


@pytest.mark.asyncio
async def test_running_transitions_status_and_sets_access_url(session, admin_user):
    ns = await _ns(session, admin_user)
    status = SessionStatus(session_id="42", status=ServiceState.RUNNING, access_url="http://1.2.3.4:8888")
    await _sync(ns, session, status)

    ns = await _stored(session, ns)
    assert ns.status == "running"
    assert ns.started_at is not None
    assert ns.access_url == "http://1.2.3.4:8888"


@pytest.mark.asyncio
async def test_error_marks_failed(session, admin_user):
    ns = await _ns(session, admin_user)
    await _sync(ns, session, SessionStatus(session_id="42", status=ServiceState.ERROR))

    assert (await _stored(session, ns)).status == "failed"


@pytest.mark.asyncio
async def test_passes_identity_to_adapter(session, admin_user):
    ns = await _ns(session, admin_user, session_type="rstudio")
    adapter = await _sync(ns, session, SessionStatus(session_id="42", status=ServiceState.STARTING))

    adapter.get_session_status.assert_awaited_once_with(
        session_id=ns.id,
        pod_name=f"bioaf-notebook-{ns.id}",
        namespace="bioaf-notebooks",
        session_type="rstudio",
    )


@pytest.mark.asyncio
async def test_no_pod_name_is_noop(session, admin_user):
    ns = await _ns(session, admin_user, compute_job_ref=None)
    with patch("app.services.notebook_lifecycle.get_notebook_adapter") as get_adapter:
        await _sync_session_from_k8s(ns, session)
    get_adapter.assert_not_called()
    assert (await _stored(session, ns)).status == "starting"


@pytest.mark.asyncio
async def test_unknown_status_does_not_mutate(session, admin_user):
    ns = await _ns(session, admin_user, status="running", access_url="http://existing:8888")
    await _sync(ns, session, SessionStatus(session_id="42", status=ServiceState.UNKNOWN))

    ns = await _stored(session, ns)
    assert ns.status == "running"
    assert ns.access_url == "http://existing:8888"


# -- shared launch decisions and races ------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_page_load_never_revives_a_session_stopped_after_it_was_loaded(session, admin_user, db_engine):
    ns = await _ns(session, admin_user)
    other = async_sessionmaker(db_engine, expire_on_commit=False)
    async with other() as db:  # a stop request commits in between
        await db.execute(update(NotebookSession).where(NotebookSession.id == ns.id).values(status="stopped"))
        await db.commit()

    await _sync(
        ns, session, SessionStatus(session_id="1", status=ServiceState.RUNNING, access_url="http://1.2.3.4:8888")
    )

    async with other() as db:
        row = (await db.execute(select(NotebookSession).where(NotebookSession.id == ns.id))).scalar_one()
    assert (row.status, row.access_url) == ("stopped", None)


@pytest.mark.asyncio
async def test_unknown_shortly_after_launch_does_not_fail_it(session, admin_user):
    ns = await _ns(session, admin_user, started_at=datetime.now(timezone.utc) - timedelta(seconds=26))
    await _sync(ns, session, SessionStatus(session_id="1", status=ServiceState.UNKNOWN))
    assert (await _stored(session, ns)).status == "starting"


@pytest.mark.asyncio
async def test_running_without_an_address_stays_starting(session, admin_user):
    ns = await _ns(session, admin_user)
    await _sync(ns, session, SessionStatus(session_id="1", status=ServiceState.RUNNING))
    assert (await _stored(session, ns)).status == "starting"


@pytest.mark.asyncio
async def test_a_launch_past_the_deadline_fails_with_cleanup_pending(session, admin_user):
    ns = await _ns(session, admin_user, started_at=datetime.now(timezone.utc) - timedelta(minutes=31))
    await _sync(ns, session, SessionStatus(session_id="1", status=ServiceState.STARTING))

    ns = await _stored(session, ns)
    assert (ns.status, ns.failure_reason) == ("failed", "startup_timeout")
    assert ns.provider_metadata["lifecycle"]["cleanup"]["state"] == "pending"
    assert ns.provider_metadata["namespace"] == "bioaf-notebooks"


@pytest.mark.asyncio
async def test_a_running_session_whose_pod_is_gone_fails_with_cleanup_pending(session, admin_user):
    ns = await _ns(session, admin_user, status="running", access_url="http://1.2.3.4:8888")
    details = {
        "failure_reason": "unknown",
        "failure_message": "The session's workload no longer exists in the cluster.",
    }
    await _sync(ns, session, SessionStatus(session_id="1", status=ServiceState.ERROR, provider_details=details))

    ns = await _stored(session, ns)
    assert ns.status == "failed"
    assert ns.failure_message == details["failure_message"]
    assert ns.provider_metadata["lifecycle"]["cleanup"]["state"] == "pending"


def test_endpoint_uses_no_private_adapter_access():
    """Static guard: the module reaches no adapter privates / K8s URLs."""
    import inspect
    import app.api.notebook_sessions as mod

    src = inspect.getsource(mod)
    for forbidden in (
        "_get_k8s_core_client",
        "_get_api_client",
        ".is_local",
        "/api/v1/namespaces/",
        "read_namespaced_pod",
    ):
        assert forbidden not in src, f"notebook_sessions still references {forbidden!r}"
