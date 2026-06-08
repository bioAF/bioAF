"""_sync_session_from_k8s routes through the NotebookProvider interface.

BAL rework, Phase 5: the notebook-session list endpoint used to reach into
adapter privates (_get_k8s_core_client / _get_api_client / is_local) and build
K8s service URLs by hand. It now reconciles the DB record from the normalized
SessionStatus returned by get_session_status().
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.adapters.models import ServiceState, SessionStatus
from app.api.notebook_sessions import _sync_session_from_k8s


def _ns(**overrides):
    base = dict(
        id=42,
        compute_job_ref="bioaf-notebook-42",
        provider_namespace="bioaf-notebooks",
        session_type="jupyter",
        status="starting",
        started_at=None,
        access_url=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _adapter(status: SessionStatus):
    adapter = MagicMock()
    adapter.get_session_status = AsyncMock(return_value=status)
    return adapter


@pytest.mark.asyncio
async def test_running_transitions_status_and_sets_access_url():
    ns = _ns()
    session = MagicMock()
    session.flush = AsyncMock()
    status = SessionStatus(session_id="42", status=ServiceState.RUNNING, access_url="http://1.2.3.4:8888")
    with patch("app.api.notebook_sessions.get_notebook_adapter", return_value=_adapter(status)):
        await _sync_session_from_k8s(ns, session)

    assert ns.status == "running"
    assert ns.started_at is not None
    assert ns.access_url == "http://1.2.3.4:8888"
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_error_marks_failed():
    ns = _ns()
    session = MagicMock()
    session.flush = AsyncMock()
    status = SessionStatus(session_id="42", status=ServiceState.ERROR)
    with patch("app.api.notebook_sessions.get_notebook_adapter", return_value=_adapter(status)):
        await _sync_session_from_k8s(ns, session)

    assert ns.status == "failed"


@pytest.mark.asyncio
async def test_passes_identity_to_adapter():
    ns = _ns(session_type="rstudio")
    session = MagicMock()
    session.flush = AsyncMock()
    adapter = _adapter(SessionStatus(session_id="42", status=ServiceState.STARTING))
    with patch("app.api.notebook_sessions.get_notebook_adapter", return_value=adapter):
        await _sync_session_from_k8s(ns, session)

    adapter.get_session_status.assert_awaited_once_with(
        session_id=42,
        pod_name="bioaf-notebook-42",
        namespace="bioaf-notebooks",
        session_type="rstudio",
    )


@pytest.mark.asyncio
async def test_no_pod_name_is_noop():
    ns = _ns(compute_job_ref=None)
    session = MagicMock()
    session.flush = AsyncMock()
    with patch("app.api.notebook_sessions.get_notebook_adapter") as get_adapter:
        await _sync_session_from_k8s(ns, session)
    get_adapter.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_status_does_not_mutate():
    ns = _ns(status="running", access_url="http://existing:8888")
    session = MagicMock()
    session.flush = AsyncMock()
    status = SessionStatus(session_id="42", status=ServiceState.UNKNOWN)
    with patch("app.api.notebook_sessions.get_notebook_adapter", return_value=_adapter(status)):
        await _sync_session_from_k8s(ns, session)

    assert ns.status == "running"
    assert ns.access_url == "http://existing:8888"
    session.flush.assert_not_awaited()


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
