"""Notebook lifecycle writes are conditional, and launch identity is durable before creation.

Blackbird, 2026-11-13: sessions 1 and 2 were stopped within seconds of launch. Five minutes later
the launch's readiness check, still running, gave up and overwrote "stopped" with "failed". A
background observer may only move a session that is still starting under the same workload; a
stop always wins. A launch records the workload it is about to create, committed, before any
provider call, so a crash between creating resources and saving the response still leaves a
record that names them. Cleanup progress is recorded apart from the failure reason.
"""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.adapters.models import (
    NotebookWorkload,
    ResourceRelease,
    SessionInfo,
    TerminationResult,
    WorkloadRelease,
)
from app.adapters.notebooks import kubernetes as nb_module
from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider
from app.models.notebook_session import NotebookSession
from app.settings_contract import ComputeMode
from tests.support.notebook_k8s_fakes import FakeCore, notebook_pod, notebook_service

POD, SVC = "bioaf-notebook-{}", "bioaf-notebook-svc-{}"


@pytest.fixture
def factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def _seed(session, admin_user, *, status="starting", job_ref="auto", metadata=None):
    ns = NotebookSession(
        user_id=admin_user.id,
        organization_id=admin_user.organization_id,
        session_type="jupyter",
        resource_profile="small",
        cpu_cores=2,
        memory_gb=8,
        status=status,
    )
    session.add(ns)
    await session.flush()
    ns.compute_job_ref = POD.format(ns.id) if job_ref == "auto" else job_ref
    ns.provider_metadata = metadata if metadata is not None else {"pod_name": ns.compute_job_ref}
    await session.commit()
    return ns


async def _row(factory, session_id):
    async with factory() as db:
        return (await db.execute(select(NotebookSession).where(NotebookSession.id == session_id))).scalar_one()


class _FastClock:
    """Sleeping advances a fake clock, so a launch's deadline passes without real waiting."""

    def __init__(self):
        from datetime import datetime, timezone

        self.now = datetime(2026, 11, 13, 2, 44, 59, tzinfo=timezone.utc)

    async def sleep(self, seconds):
        from datetime import timedelta

        self.now += timedelta(seconds=seconds)


def _fast_clock(monkeypatch):
    clock = _FastClock()
    monkeypatch.setattr(nb_module.asyncio, "sleep", clock.sleep)
    monkeypatch.setattr(nb_module, "_utcnow", lambda: clock.now)
    return clock


def _adapter(mode_adapter_factory, factory, monkeypatch, core):
    provider = mode_adapter_factory(KubernetesNotebookProvider, mode=ComputeMode.KUBERNETES)
    provider._session_factory = factory
    provider._api_client = MagicMock()
    provider._client_created_at = time.monotonic()
    monkeypatch.setattr(provider, "_get_k8s_core_client", lambda: core)
    monkeypatch.setattr(provider, "_get_api_client", lambda: MagicMock())
    monkeypatch.setattr(nb_module, "api_client_auth_header", lambda _c: "Bearer t")
    _fast_clock(monkeypatch)
    return provider


async def _poll(adapter, ns):
    await adapter._poll_session_ready(
        ns.id,
        POD.format(ns.id),
        SVC.format(ns.id),
        "bioaf-notebooks",
        8888,
        organization_id=ns.organization_id,
        user_id=ns.user_id,
        session_type="jupyter",
    )


def _pending_core(ns):
    core = FakeCore(
        pods=[notebook_pod(ns.id, ns.user_id, phase="Pending", ready=False, notebook="waiting")],
        services=[notebook_service(ns.id)],
    )
    core.delete_is_immediate = True
    return core


@pytest.mark.parametrize("status", ["stopped", "failed"])
@pytest.mark.asyncio
async def test_a_late_readiness_failure_never_overwrites_a_finished_session(
    session, admin_user, factory, mode_adapter_factory, monkeypatch, status
):
    ns = await _seed(session, admin_user, status=status)
    core = _pending_core(ns)
    adapter = _adapter(mode_adapter_factory, factory, monkeypatch, core)

    await _poll(adapter, ns)

    row = await _row(factory, ns.id)
    assert row.status == status
    assert row.failure_reason is None and row.failure_message is None
    assert core.deletes() == []  # the stop owns its own cleanup


@pytest.mark.asyncio
async def test_a_late_ready_result_never_revives_a_stopped_session(
    session, admin_user, factory, mode_adapter_factory, monkeypatch
):
    ns = await _seed(session, admin_user, status="stopped")
    core = FakeCore(pods=[notebook_pod(ns.id, ns.user_id)], services=[notebook_service(ns.id)])
    adapter = _adapter(mode_adapter_factory, factory, monkeypatch, core)
    monkeypatch.setattr(adapter, "_resolve_service_url", lambda *a: "http://1.2.3.4:8888")

    await _poll(adapter, ns)

    row = await _row(factory, ns.id)
    assert (row.status, row.access_url) == ("stopped", None)


@pytest.mark.asyncio
async def test_an_observer_for_a_different_workload_changes_nothing(
    session, admin_user, factory, mode_adapter_factory, monkeypatch
):
    ns = await _seed(session, admin_user, job_ref="bioaf-notebook-other")
    adapter = _adapter(mode_adapter_factory, factory, monkeypatch, _pending_core(ns))

    await _poll(adapter, ns)

    assert (await _row(factory, ns.id)).status == "starting"


@pytest.mark.asyncio
async def test_a_starting_session_still_becomes_running(
    session, admin_user, factory, mode_adapter_factory, monkeypatch
):
    ns = await _seed(session, admin_user)
    core = FakeCore(pods=[notebook_pod(ns.id, ns.user_id)], services=[notebook_service(ns.id)])
    adapter = _adapter(mode_adapter_factory, factory, monkeypatch, core)
    monkeypatch.setattr(adapter, "_resolve_service_url", lambda *a: "http://1.2.3.4:8888")

    await _poll(adapter, ns)

    row = await _row(factory, ns.id)
    assert (row.status, row.access_url) == ("running", "http://1.2.3.4:8888")


@pytest.mark.asyncio
async def test_a_failed_launch_records_pending_cleanup_before_deleting_then_its_result(
    session, admin_user, factory, mode_adapter_factory, monkeypatch
):
    ns = await _seed(session, admin_user, metadata={"pod_name": POD.format(1), "keep": "me"})
    core = _pending_core(ns)
    seen_before_delete = []
    original = core.delete_namespaced_pod

    def delete_pod(name, namespace, body=None, **kw):
        import asyncio

        row = asyncio.run_coroutine_threadsafe(_row(factory, ns.id), loop).result(timeout=10)
        seen_before_delete.append((row.status, ((row.provider_metadata or {}).get("lifecycle") or {}).get("cleanup")))
        return original(name, namespace, body=body, **kw)

    core.delete_namespaced_pod = delete_pod
    import asyncio

    loop = asyncio.get_running_loop()
    adapter = _adapter(mode_adapter_factory, factory, monkeypatch, core)

    await _poll(adapter, ns)

    [(status_then, cleanup_then)] = seen_before_delete
    assert status_then == "failed" and (cleanup_then or {}).get("state") == "pending"
    row = await _row(factory, ns.id)
    assert (row.status, row.failure_reason) == ("failed", "startup_timeout")
    assert row.provider_metadata["keep"] == "me"  # unrelated keys survive
    cleanup = row.provider_metadata["lifecycle"]["cleanup"]
    assert (cleanup["state"], cleanup["pod"], cleanup["service"]) == ("complete", "absent", "absent")


# -- launch reservation and stop ------------------------------------------------------------------


class _Adapter:
    """Notebook adapter double: records what the database held when each provider call started."""

    def __init__(self, factory, *, launch_error=None, release=None):
        self.factory = factory
        self.launch_error = launch_error
        self.release = release
        self.row_at_launch = None

    def reserve_workload(self, session_id, user_id, session_type):
        return NotebookWorkload(
            session_id=session_id,
            user_id=user_id,
            session_type=session_type,
            namespace="bioaf-notebooks",
            pod_name=POD.format(session_id),
            service_name=SVC.format(session_id),
        )

    async def workload_cluster(self):
        return "lab-proj/bioaf-lab-abc123"

    async def launch_session(self, spec):
        self.row_at_launch = await _row(self.factory, spec["session_id"])
        if self.launch_error:
            raise self.launch_error
        sid = spec["session_id"]
        return SessionInfo(
            session_id=str(sid),
            status="starting",
            provider_details={"pod_name": POD.format(sid), "namespace": "bioaf-notebooks"},
        )

    async def terminate_session(self, session_id, **kwargs):
        details = {"release": self.release.model_dump()} if self.release else {}
        return TerminationResult(provider_details=details)


async def _launch(session, admin_user, adapter, monkeypatch):
    from app.services import notebook_service as svc_module
    from app.services.notebook_service import NotebookService

    monkeypatch.setattr(svc_module, "get_notebook_adapter", lambda: adapter)
    monkeypatch.setattr(svc_module.QuotaService, "check_quota", AsyncMock(return_value=(True, "")))
    ns = await NotebookService.launch_session(
        session,
        user_id=admin_user.id,
        org_id=admin_user.organization_id,
        session_type="jupyter",
        resource_profile="small",
    )
    await session.commit()
    return ns


@pytest.mark.asyncio
async def test_the_workload_is_recorded_and_committed_before_the_provider_creates_it(
    session, admin_user, factory, monkeypatch
):
    adapter = _Adapter(factory)
    ns = await _launch(session, admin_user, adapter, monkeypatch)

    seen = adapter.row_at_launch
    assert seen.compute_job_ref == POD.format(ns.id)
    assert seen.provider_metadata["namespace"] == "bioaf-notebooks"
    assert seen.provider_metadata["service_name"] == SVC.format(ns.id)
    launch = seen.provider_metadata["lifecycle"]
    assert launch["reserved_at"] and launch["cluster"] == "lab-proj/bioaf-lab-abc123"
    assert (await _row(factory, ns.id)).status == "starting"


@pytest.mark.asyncio
async def test_a_launch_error_after_the_reservation_leaves_cleanup_pending(session, admin_user, factory, monkeypatch):
    adapter = _Adapter(factory, launch_error=RuntimeError("connection reset after create"))
    ns = await _launch(session, admin_user, adapter, monkeypatch)

    row = await _row(factory, ns.id)
    assert row.status == "failed"
    assert row.compute_job_ref == POD.format(ns.id)
    assert row.provider_metadata["lifecycle"]["cleanup"]["state"] == "pending"
    assert row.failure_message  # the launch error is kept apart from cleanup progress


def _release(pod, service):
    return WorkloadRelease(pod=ResourceRelease(state=pod), service=ResourceRelease(state=service))


@pytest.mark.parametrize(
    "release,expected",
    [(("absent", "absent"), "complete"), (("absent", "terminating"), "pending"), (("blocked", "absent"), "blocked")],
)
@pytest.mark.asyncio
async def test_stop_records_whether_its_resources_are_gone(
    session, admin_user, factory, monkeypatch, release, expected
):
    from app.services import notebook_service as svc_module
    from app.services.notebook_service import NotebookService

    ns = await _seed(session, admin_user, status="running")
    adapter = _Adapter(factory, release=_release(*release))
    monkeypatch.setattr(svc_module, "get_notebook_adapter", lambda: adapter)

    await NotebookService.stop_session(session, ns.id, admin_user.id)
    await session.commit()

    row = await _row(factory, ns.id)
    assert row.status == "stopped"
    cleanup = row.provider_metadata["lifecycle"]["cleanup"]
    assert (cleanup["state"], cleanup["pod"], cleanup["service"]) == (expected, release[0], release[1])


# -- the launch's own readiness check uses the shared decision ------------------------------------


class _Clock:
    def __init__(self):
        from datetime import datetime, timezone

        self.now = datetime(2026, 11, 13, 2, 44, 59, tzinfo=timezone.utc)

    async def sleep(self, seconds):
        from datetime import timedelta

        self.now += timedelta(seconds=seconds)


def _scripted(core, ns, script):
    """Replace the pod on each read according to ``script(now)``."""
    original = core.read_namespaced_pod

    def read(name, namespace, **kw):
        pod = script()
        if pod is None:
            core.pods.pop(name, None)
        else:
            core.pods[name] = pod
        return original(name, namespace, **kw)

    core.read_namespaced_pod = read


@pytest.mark.asyncio
async def test_a_cold_start_still_pulling_after_five_minutes_becomes_usable_at_thirteen(
    session, admin_user, factory, mode_adapter_factory, monkeypatch
):
    from datetime import timedelta

    ns = await _seed(session, admin_user)
    clock = _Clock()
    start = clock.now
    core = FakeCore(services=[notebook_service(ns.id)], events=[("Pulling", 'Pulling image "bioaf-scrna"')])

    def script():
        if clock.now - start < timedelta(seconds=26):
            return notebook_pod(ns.id, ns.user_id, phase="Unknown", ready=False, notebook=None)
        if clock.now - start < timedelta(minutes=13):
            return notebook_pod(ns.id, ns.user_id, phase="Pending", ready=False, notebook="waiting")
        return notebook_pod(ns.id, ns.user_id)

    _scripted(core, ns, script)
    adapter = _adapter(mode_adapter_factory, factory, monkeypatch, core)
    monkeypatch.setattr(nb_module.asyncio, "sleep", clock.sleep)
    monkeypatch.setattr(nb_module, "_utcnow", lambda: clock.now)
    monkeypatch.setattr(adapter, "_resolve_service_url", lambda *a: "http://1.2.3.4:8888")

    await _poll(adapter, ns)

    row = await _row(factory, ns.id)
    assert (row.status, row.access_url) == ("running", "http://1.2.3.4:8888")
    assert clock.now - start >= timedelta(minutes=13)
    assert core.deletes() == []


@pytest.mark.asyncio
async def test_a_launch_that_never_finishes_fails_at_the_overall_deadline_and_is_released(
    session, admin_user, factory, mode_adapter_factory, monkeypatch
):
    from datetime import timedelta

    ns = await _seed(session, admin_user)
    clock = _Clock()
    start = clock.now
    core = _pending_core(ns)
    adapter = _adapter(mode_adapter_factory, factory, monkeypatch, core)
    monkeypatch.setattr(nb_module.asyncio, "sleep", clock.sleep)
    monkeypatch.setattr(nb_module, "_utcnow", lambda: clock.now)

    await _poll(adapter, ns)

    row = await _row(factory, ns.id)
    assert (row.status, row.failure_reason) == ("failed", "startup_timeout")
    assert timedelta(minutes=30) <= clock.now - start < timedelta(minutes=31)
    assert row.provider_metadata["lifecycle"]["cleanup"]["state"] == "complete"
