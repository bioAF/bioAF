"""Notebook lifecycle writes are conditional, and launch identity is durable before creation.

Blackbird, 2026-11-13: sessions 1 and 2 were stopped within seconds of launch. Five minutes later
the launch's readiness check, still running, gave up and overwrote "stopped" with "failed". An
observer may only move a session that is still starting under the same workload; a stop that
lands first always wins. A launch records the workload it is about to create, committed, before
any provider call, so a crash between creating resources and saving the response still leaves a
record that names them. Cleanup progress is recorded apart from the failure reason.
"""

import asyncio
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.adapters.models import (
    NotebookWorkload,
    ResourceRelease,
    SessionInfo,
    TerminationResult,
    WorkloadRelease,
)
from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider
from app.models.notebook_session import NotebookSession
from app.settings_contract import ComputeMode
from tests.support.notebook_k8s_fakes import FakeCore, notebook_pod, notebook_service, serve_services_over_http

POD, SVC = "bioaf-notebook-{}", "bioaf-notebook-svc-{}"
T0 = datetime(2026, 11, 13, 2, 44, 59, tzinfo=timezone.utc)
# A reserved launch always records its (complete) cluster identity.
CLUSTER = "lab-proj/us-east1/bioaf-lab"


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
        started_at=T0,
    )
    session.add(ns)
    await session.flush()
    ns.compute_job_ref = POD.format(ns.id) if job_ref == "auto" else job_ref
    ns.provider_metadata = (
        metadata
        if metadata is not None
        else {
            "pod_name": ns.compute_job_ref,
            "namespace": "bioaf-notebooks",
            "lifecycle": {"reserved_at": T0.isoformat(), "cluster": CLUSTER},
        }
    )
    await session.commit()
    return ns


async def _row(factory, session_id):
    async with factory() as db:
        return (await db.execute(select(NotebookSession).where(NotebookSession.id == session_id))).scalar_one()


def _k8s(mode_adapter_factory, monkeypatch, core, url=None):
    provider = mode_adapter_factory(KubernetesNotebookProvider, mode=ComputeMode.KUBERNETES)
    provider._api_client = MagicMock()
    provider._client_created_at = time.monotonic()
    monkeypatch.setattr(provider, "_get_k8s_core_client", lambda: core)
    # The real address resolver, answered by the fake cluster's Services (see the fakes module).
    provider.service_reads = serve_services_over_http(monkeypatch, provider, core, url)

    async def cluster():
        return CLUSTER

    monkeypatch.setattr(provider, "workload_cluster", cluster)
    return provider


async def _pass(factory, adapter, monkeypatch, now):
    from app.services import notebook_lifecycle

    monkeypatch.setattr(notebook_lifecycle, "get_notebook_adapter", lambda: adapter)
    return await notebook_lifecycle.reconcile_pass(factory, now=now)


def _pending_core(ns):
    core = FakeCore(
        pods=[notebook_pod(ns.id, ns.user_id, phase="Pending", ready=False, notebook="waiting")],
        services=[notebook_service(ns.id)],
    )
    core.delete_is_immediate = True
    return core


def _stop_during_read(core, factory, ns, loop, **values):
    """A stop request commits while the pass is reading the pod (the incident's interleaving)."""
    original = core.read_namespaced_pod

    def read(name, namespace, **kw):
        async def stop():
            async with factory() as db:
                await db.execute(update(NotebookSession).where(NotebookSession.id == ns.id).values(**values))
                await db.commit()

        asyncio.run_coroutine_threadsafe(stop(), loop).result(timeout=10)
        core.read_namespaced_pod = original
        return original(name, namespace, **kw)

    core.read_namespaced_pod = read


@pytest.mark.parametrize("minutes", [31, 13], ids=["late-failure", "late-ready"])
@pytest.mark.asyncio
async def test_a_stop_that_lands_during_observation_always_wins(
    session, admin_user, factory, mode_adapter_factory, monkeypatch, minutes
):
    ns = await _seed(session, admin_user)
    core = (
        _pending_core(ns)
        if minutes == 31
        else FakeCore(pods=[notebook_pod(ns.id, ns.user_id)], services=[notebook_service(ns.id)])
    )
    _stop_during_read(core, factory, ns, asyncio.get_running_loop(), status="stopped")
    adapter = _k8s(mode_adapter_factory, monkeypatch, core, url="http://1.2.3.4:8888")

    await _pass(factory, adapter, monkeypatch, T0 + timedelta(minutes=minutes))

    row = await _row(factory, ns.id)
    assert (row.status, row.access_url) == ("stopped", None)
    assert row.failure_reason is None and row.failure_message is None


@pytest.mark.asyncio
async def test_an_observation_for_a_different_workload_changes_nothing(
    session, admin_user, factory, mode_adapter_factory, monkeypatch
):
    ns = await _seed(session, admin_user)
    core = _pending_core(ns)
    _stop_during_read(core, factory, ns, asyncio.get_running_loop(), compute_job_ref="bioaf-notebook-other")
    adapter = _k8s(mode_adapter_factory, monkeypatch, core)

    await _pass(factory, adapter, monkeypatch, T0 + timedelta(minutes=31))

    row = await _row(factory, ns.id)
    assert (row.status, row.failure_reason) == ("starting", None)


@pytest.mark.asyncio
async def test_a_failed_launch_records_pending_cleanup_before_deleting_then_its_result(
    session, admin_user, factory, mode_adapter_factory, monkeypatch
):
    ns = await _seed(
        session,
        admin_user,
        metadata={
            "pod_name": POD.format(1),
            "keep": "me",
            "lifecycle": {"reserved_at": T0.isoformat(), "cluster": CLUSTER},
        },
    )
    core = _pending_core(ns)
    loop = asyncio.get_running_loop()
    seen_before_delete = []
    original = core.delete_namespaced_pod

    def delete_pod(name, namespace, body=None, **kw):
        row = asyncio.run_coroutine_threadsafe(_row(factory, ns.id), loop).result(timeout=10)
        seen_before_delete.append((row.status, ((row.provider_metadata or {}).get("lifecycle") or {}).get("cleanup")))
        return original(name, namespace, body=body, **kw)

    core.delete_namespaced_pod = delete_pod
    adapter = _k8s(mode_adapter_factory, monkeypatch, core)

    await _pass(factory, adapter, monkeypatch, T0 + timedelta(minutes=31))

    [(status_then, cleanup_then)] = seen_before_delete
    assert status_then == "failed" and (cleanup_then or {}).get("state") == "pending"
    row = await _row(factory, ns.id)
    assert (row.status, row.failure_reason) == ("failed", "startup_timeout")
    assert row.provider_metadata["keep"] == "me"  # unrelated keys survive
    cleanup = row.provider_metadata["lifecycle"]["cleanup"]
    assert (cleanup["state"], cleanup["pod"], cleanup["service"]) == ("complete", "absent", "absent")


@pytest.mark.asyncio
async def test_a_cold_start_still_pulling_after_five_minutes_becomes_usable_at_thirteen(
    session, admin_user, factory, mode_adapter_factory, monkeypatch
):
    """Acceptance 1 and 11: Unknown at 26 s, still pulling at 6 min, ready at 13 min."""
    ns = await _seed(session, admin_user)
    core = FakeCore(services=[notebook_service(ns.id)], events=[("TriggeredScaleUp", "Pod triggered scale-up")])
    adapter = _k8s(mode_adapter_factory, monkeypatch, core, url="http://1.2.3.4:8888")
    observations = [
        (timedelta(seconds=26), notebook_pod(ns.id, ns.user_id, phase="Unknown", ready=False, notebook=None)),
        (timedelta(minutes=6), notebook_pod(ns.id, ns.user_id, phase="Pending", ready=False, notebook="waiting")),
        (timedelta(minutes=13), notebook_pod(ns.id, ns.user_id)),
    ]
    for offset, pod in observations[:2]:
        core.pods[pod.metadata.name] = pod
        await _pass(factory, adapter, monkeypatch, T0 + offset)
        assert (await _row(factory, ns.id)).status == "starting", offset

    offset, pod = observations[2]
    core.pods[pod.metadata.name] = pod
    await _pass(factory, adapter, monkeypatch, T0 + offset)

    row = await _row(factory, ns.id)
    assert (row.status, row.access_url) == ("running", "http://1.2.3.4:8888")
    assert core.deletes() == []


@pytest.mark.parametrize("phase", ["Pending", "Failed"])
@pytest.mark.asyncio
async def test_a_failed_or_expired_launch_is_released(
    session, admin_user, factory, mode_adapter_factory, monkeypatch, phase
):
    """A confirmed failure fails at once; a launch still pending fails only at the deadline."""
    ns = await _seed(session, admin_user)
    pod = notebook_pod(
        ns.id, ns.user_id, phase=phase, ready=False, notebook="waiting" if phase == "Pending" else "terminated"
    )
    core = FakeCore(pods=[pod], services=[notebook_service(ns.id)])
    core.delete_is_immediate = True
    adapter = _k8s(mode_adapter_factory, monkeypatch, core)

    await _pass(factory, adapter, monkeypatch, T0 + timedelta(minutes=29))
    assert ((await _row(factory, ns.id)).status == "failed") is (phase == "Failed")

    await _pass(factory, adapter, monkeypatch, T0 + timedelta(minutes=30))
    row = await _row(factory, ns.id)
    assert row.status == "failed"
    assert row.failure_reason == ("startup_timeout" if phase == "Pending" else "unknown")
    assert not core.pods and not core.services
    assert row.provider_metadata["lifecycle"]["cleanup"]["state"] == "complete"


# -- launch reservation and stop ------------------------------------------------------------------


class _Adapter:
    """Notebook adapter double: records what the database held when each provider call started."""

    def __init__(self, factory, *, launch_error=None, release=None):
        self.factory = factory
        self.launch_error = launch_error
        self.release = release
        self.row_at_launch = None

    def reserve_workload(self, session_id, user_id, session_type, *, organization_id=None):
        return NotebookWorkload(
            session_id=session_id,
            user_id=user_id,
            session_type=session_type,
            namespace="bioaf-notebooks",
            pod_name=POD.format(session_id),
            service_name=SVC.format(session_id),
            organization_id=organization_id,
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
