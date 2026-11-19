"""The periodic notebook pass: resume launches, finish cleanup, and nothing else.

Runs on the existing one-minute periodic schedule. Each pass observes interactive sessions that
are still starting (so a backend restart loses nothing), then releases resources for failed or
stopped sessions whose cleanup is pending or was never inspected. Every item is isolated, every
write is conditional, and a resource is only reported released once it is confirmed gone.
"""

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.adapters.models import (
    NotebookWorkload,
    ResourceRelease,
    ServiceState,
    SessionStatus,
    TerminationResult,
    WorkloadRelease,
)
from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider
from app.models.notebook_session import NotebookSession
from app.settings_contract import ComputeMode
from tests.support.notebook_k8s_fakes import FakeCore, notebook_pod, notebook_service

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
CLUSTER = "lab-proj/us-east1/bioaf-lab"


@pytest.fixture
def factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def _seed(
    session, admin_user, *, status="starting", session_type="jupyter", minutes_ago=1, lifecycle=None, url=None
):
    ns = NotebookSession(
        user_id=admin_user.id,
        organization_id=admin_user.organization_id,
        session_type=session_type,
        resource_profile="small",
        cpu_cores=2,
        memory_gb=8,
        status=status,
        access_url=url,
        started_at=NOW - timedelta(minutes=minutes_ago),
    )
    session.add(ns)
    await session.flush()
    ns.compute_job_ref = f"bioaf-notebook-{ns.id}"
    meta = {"pod_name": ns.compute_job_ref, "namespace": "bioaf-notebooks"}
    if lifecycle is not None:
        meta["lifecycle"] = {"reserved_at": (NOW - timedelta(minutes=minutes_ago)).isoformat(), **lifecycle}
    ns.provider_metadata = meta
    await session.commit()
    return ns


async def _row(factory, sid):
    async with factory() as db:
        return (await db.execute(select(NotebookSession).where(NotebookSession.id == sid))).scalar_one()


def _cleanup(row):
    return ((row.provider_metadata or {}).get("lifecycle") or {}).get("cleanup") or {}


def _release(pod="absent", service="absent"):
    return WorkloadRelease(pod=ResourceRelease(state=pod), service=ResourceRelease(state=service))


class FakeAdapter:
    """Notebook adapter double for the pass: scripted status reads and releases, recorded calls."""

    def __init__(self, *, status=None, releases=None, cluster=CLUSTER, release_error_for=()):
        self.status = status or SessionStatus(session_id="x", status=ServiceState.STARTING)
        self.releases = list(releases or [])
        self.cluster = cluster
        self.release_error_for = set(release_error_for)
        self.released: list[int] = []
        self.terminated: list[dict] = []

    def recorded_workload(self, ns):
        return NotebookWorkload(
            session_id=ns.id,
            user_id=ns.user_id,
            session_type=ns.session_type,
            namespace="bioaf-notebooks",
            pod_name=ns.compute_job_ref,
            service_name=f"bioaf-notebook-svc-{ns.id}",
        )

    async def get_session_status(self, session_id, **kw):
        return self.status

    async def workload_cluster(self):
        return self.cluster

    async def labelled_workloads(self):
        return {}

    async def release_session_resources(self, workload):
        self.released.append(workload.session_id)
        if workload.session_id in self.release_error_for:
            raise RuntimeError("connection reset")
        return self.releases.pop(0) if self.releases else _release()

    async def terminate_session(self, session_id, **kw):
        self.terminated.append(kw)
        return TerminationResult(provider_details={"release": _release().model_dump()})


async def _pass(factory, adapter, monkeypatch, now=NOW):
    from app.services import notebook_lifecycle

    monkeypatch.setattr(notebook_lifecycle, "get_notebook_adapter", lambda: adapter)
    return await notebook_lifecycle.reconcile_pass(factory, now=now)


@pytest.mark.asyncio
async def test_a_launch_in_progress_when_the_backend_restarted_is_resumed(session, admin_user, factory, monkeypatch):
    ns = await _seed(session, admin_user, minutes_ago=13, lifecycle={"cluster": CLUSTER})
    adapter = FakeAdapter(
        status=SessionStatus(session_id="x", status=ServiceState.RUNNING, access_url="http://1.2.3.4:8888")
    )

    await _pass(factory, adapter, monkeypatch)

    row = await _row(factory, ns.id)
    assert (row.status, row.access_url) == ("running", "http://1.2.3.4:8888")
    assert adapter.released == []


@pytest.mark.asyncio
async def test_a_launch_past_the_deadline_fails_and_is_released_in_the_same_pass(
    session, admin_user, factory, monkeypatch
):
    ns = await _seed(session, admin_user, minutes_ago=31, lifecycle={"cluster": CLUSTER})
    adapter = FakeAdapter()

    await _pass(factory, adapter, monkeypatch)

    row = await _row(factory, ns.id)
    assert (row.status, row.failure_reason) == ("failed", "startup_timeout")
    assert adapter.released == [ns.id]
    assert _cleanup(row)["state"] == "complete"


@pytest.mark.asyncio
async def test_cleanup_is_retried_until_absence_is_confirmed(session, admin_user, factory, monkeypatch):
    ns = await _seed(
        session, admin_user, status="failed", lifecycle={"cluster": CLUSTER, "cleanup": {"state": "pending"}}
    )
    adapter = FakeAdapter(releases=[_release(pod="failed"), _release(pod="terminating"), _release()])

    await _pass(factory, adapter, monkeypatch)
    assert _cleanup(await _row(factory, ns.id))["state"] == "pending"
    await _pass(factory, adapter, monkeypatch)
    assert _cleanup(await _row(factory, ns.id))["state"] == "pending"
    await _pass(factory, adapter, monkeypatch)
    assert _cleanup(await _row(factory, ns.id))["state"] == "complete"
    await _pass(factory, adapter, monkeypatch)
    assert adapter.released == [ns.id] * 3  # nothing more once complete


@pytest.mark.asyncio
async def test_a_blocked_resource_is_reported_once_and_never_retried(session, admin_user, factory, monkeypatch):
    ns = await _seed(session, admin_user, status="failed", lifecycle={"cleanup": {"state": "pending"}})
    adapter = FakeAdapter(releases=[_release(pod="blocked")])

    await _pass(factory, adapter, monkeypatch)
    await _pass(factory, adapter, monkeypatch)

    assert _cleanup(await _row(factory, ns.id))["state"] == "blocked"
    assert adapter.released == [ns.id]


@pytest.mark.asyncio
async def test_a_changed_cluster_blocks_automatic_deletion(session, admin_user, factory, monkeypatch):
    ns = await _seed(
        session,
        admin_user,
        status="failed",
        lifecycle={"cluster": "old/us-central1/gone", "cleanup": {"state": "pending"}},
    )
    adapter = FakeAdapter()

    await _pass(factory, adapter, monkeypatch)

    cleanup = _cleanup(await _row(factory, ns.id))
    assert cleanup["state"] == "blocked" and "cluster" in cleanup["detail"]
    assert adapter.released == []


@pytest.mark.asyncio
async def test_one_failing_item_does_not_stop_the_rest(session, admin_user, factory, monkeypatch):
    first = await _seed(session, admin_user, status="failed", lifecycle={"cleanup": {"state": "pending"}})
    second = await _seed(session, admin_user, status="stopped", lifecycle={"cleanup": {"state": "pending"}})
    adapter = FakeAdapter(release_error_for={first.id})

    await _pass(factory, adapter, monkeypatch)

    assert _cleanup(await _row(factory, first.id))["state"] == "pending"
    assert _cleanup(await _row(factory, second.id))["state"] == "complete"


@pytest.mark.asyncio
async def test_work_nodes_headless_runs_and_finished_cleanups_are_left_alone(session, admin_user, factory, monkeypatch):
    await _seed(session, admin_user, status="starting", session_type="ssh", minutes_ago=90)
    await _seed(session, admin_user, status="failed", session_type="headless")
    done = await _seed(session, admin_user, status="stopped", lifecycle={"cleanup": {"state": "complete"}})
    running = await _seed(session, admin_user, status="running", url="http://1.2.3.4:8888")
    adapter = FakeAdapter()

    await _pass(factory, adapter, monkeypatch)

    assert adapter.released == [] and adapter.terminated == []
    assert _cleanup(await _row(factory, done.id))["state"] == "complete"
    assert (await _row(factory, running.id)).status == "running"


@pytest.mark.asyncio
async def test_a_session_that_failed_while_running_saves_its_outputs_before_release(
    session, admin_user, factory, monkeypatch
):
    ns = await _seed(
        session,
        admin_user,
        status="failed",
        url="http://1.2.3.4:8888",
        lifecycle={"cleanup": {"state": "pending", "save_outputs": True}},
    )
    adapter = FakeAdapter()

    await _pass(factory, adapter, monkeypatch)

    [call] = adapter.terminated
    assert call["ran"] is True and call["user_id"] == ns.user_id
    assert adapter.released == []  # terminate released them, after saving
    row = await _row(factory, ns.id)
    assert row.status == "failed"
    assert _cleanup(row)["state"] == "complete"


@pytest.mark.asyncio
async def test_a_running_session_whose_workload_fails_is_saved_then_released(session, admin_user, factory, monkeypatch):
    """The session list observes it failing; the next pass saves what it can before releasing."""
    from app.services import notebook_lifecycle

    ns = await _seed(session, admin_user, status="running", url="http://1.2.3.4:8888")
    adapter = FakeAdapter(status=SessionStatus(session_id="x", status=ServiceState.ERROR))
    monkeypatch.setattr(notebook_lifecycle, "get_notebook_adapter", lambda: adapter)
    await notebook_lifecycle.observe_session(session, ns, now=NOW)
    await session.commit()

    await _pass(factory, adapter, monkeypatch)

    row = await _row(factory, ns.id)
    assert row.status == "failed"
    assert adapter.terminated and adapter.terminated[0]["ran"] is True


# -- against the Kubernetes adapter and a fake cluster -------------------------------------------


def _k8s(mode_adapter_factory, monkeypatch, core):
    provider = mode_adapter_factory(KubernetesNotebookProvider, mode=ComputeMode.KUBERNETES)
    provider._api_client = MagicMock()
    provider._client_created_at = time.monotonic()
    monkeypatch.setattr(provider, "_get_k8s_core_client", lambda: core)

    async def cluster():
        return None

    monkeypatch.setattr(provider, "workload_cluster", cluster)
    return provider


@pytest.mark.asyncio
async def test_an_existing_failed_session_still_holding_its_pod_and_service_is_cleaned_up(
    session, admin_user, factory, monkeypatch, mode_adapter_factory
):
    """Blackbird session 3: marked failed at five minutes while its pod kept running."""
    ns = await _seed(session, admin_user, status="failed")
    core = FakeCore(pods=[notebook_pod(ns.id, admin_user.id)], services=[notebook_service(ns.id)])
    core.delete_is_immediate = True

    await _pass(factory, _k8s(mode_adapter_factory, monkeypatch, core), monkeypatch)

    assert not core.pods and not core.services
    assert _cleanup(await _row(factory, ns.id))["state"] == "complete"


@pytest.mark.asyncio
async def test_a_service_left_after_its_pod_is_gone_is_cleaned_up(
    session, admin_user, factory, monkeypatch, mode_adapter_factory
):
    ns = await _seed(session, admin_user, status="stopped")
    core = FakeCore(services=[notebook_service(ns.id)])
    core.delete_is_immediate = True

    await _pass(factory, _k8s(mode_adapter_factory, monkeypatch, core), monkeypatch)

    assert [c[1] for c in core.deletes()] == ["service"]
    assert _cleanup(await _row(factory, ns.id))["state"] == "complete"


@pytest.mark.asyncio
async def test_another_sessions_pod_under_the_recorded_name_is_never_deleted(
    session, admin_user, factory, monkeypatch, mode_adapter_factory
):
    ns = await _seed(session, admin_user, status="failed")
    core = FakeCore(pods=[notebook_pod(ns.id, user_id=admin_user.id + 999)])

    await _pass(factory, _k8s(mode_adapter_factory, monkeypatch, core), monkeypatch)

    assert core.deletes() == []
    assert _cleanup(await _row(factory, ns.id))["state"] == "blocked"


@pytest.mark.asyncio
async def test_resources_with_no_session_record_are_reported_not_deleted(
    session, admin_user, factory, monkeypatch, mode_adapter_factory, caplog
):
    known = await _seed(session, admin_user, status="running", url="http://1.2.3.4:8888")
    core = FakeCore(
        pods=[notebook_pod(known.id, admin_user.id), notebook_pod(987654, 1)],
        services=[notebook_service(known.id), notebook_service(987654)],
    )

    with caplog.at_level("WARNING", logger="bioaf.notebook_lifecycle"):
        await _pass(factory, _k8s(mode_adapter_factory, monkeypatch, core), monkeypatch)

    assert core.deletes() == []
    orphans = [r.getMessage() for r in caplog.records if "no session record" in r.getMessage()]
    assert len(orphans) == 1 and "bioaf-notebook-987654" in orphans[0] and "bioaf-notebook-svc-987654" in orphans[0]


def test_the_pass_runs_on_the_existing_one_minute_schedule():
    from app.tasks.jobs import MINUTE, PERIODIC_JOBS

    [job] = [j for j in PERIODIC_JOBS if j.role == "notebook-lifecycle"]
    assert job.interval == MINUTE


@pytest.mark.asyncio
async def test_a_launch_no_longer_starts_its_own_readiness_task(mode_adapter_factory, monkeypatch):
    """One observer: the periodic pass (and the session list) decide, not a per-launch task."""
    from app import runtime
    from tests.test_notebook_launch_cleanup import LaunchCore, _spec

    provider = mode_adapter_factory(KubernetesNotebookProvider, mode=ComputeMode.KUBERNETES)
    provider._api_client = MagicMock()
    provider._client_created_at = time.monotonic()
    monkeypatch.setattr(provider, "_get_k8s_core_client", lambda: LaunchCore())

    async def noop(*a, **k):
        return None

    monkeypatch.setattr(provider, "ensure_notebook_namespace", noop)
    monkeypatch.setattr(provider, "_ensure_gcs_secret", lambda ns: False)
    spawned = []

    def record(coro, role=None, **k):
        spawned.append(role)
        coro.close()

    monkeypatch.setattr(runtime, "spawn", record)

    await provider._k8s_launch_session(_spec())

    assert "notebook-readiness" not in spawned


@pytest.mark.asyncio
async def test_a_reserved_launch_whose_pod_is_not_created_yet_is_left_to_finish(
    session, admin_user, factory, monkeypatch, mode_adapter_factory
):
    """The pass can run between the reservation commit and pod creation: absence then is not failure."""
    ns = await _seed(session, admin_user, status="pending", minutes_ago=0)
    core = FakeCore()

    await _pass(factory, _k8s(mode_adapter_factory, monkeypatch, core), monkeypatch)

    assert (await _row(factory, ns.id)).status == "pending"
    assert core.deletes() == []


@pytest.mark.asyncio
async def test_a_reserved_launch_that_never_created_its_pod_fails_at_the_deadline(
    session, admin_user, factory, monkeypatch, mode_adapter_factory
):
    """A crash between reservation and creation: the deadline still ends it, and cleanup finds nothing."""
    ns = await _seed(session, admin_user, status="pending", minutes_ago=31, lifecycle={})
    core = FakeCore()

    await _pass(factory, _k8s(mode_adapter_factory, monkeypatch, core), monkeypatch)

    row = await _row(factory, ns.id)
    assert (row.status, row.failure_reason) == ("failed", "startup_timeout")
    assert _cleanup(row)["state"] == "complete"
