"""Every path that touches a notebook's resources applies the same identity, cluster, and output rules.

Review of 7bbedcf8 found stop and idle-stop bypassing the recorded-cluster guard, launch completion
overwriting a committed stop, an idle-stop retry skipping output saving, cleanup accepting
conflicting recorded identity, and a provider error at status-read time disabling the startup
deadline. Each is pinned here, together with the edge cases that follow from the fix.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import update

from app.models.notebook_session import NotebookSession
from tests.support.notebook_k8s_fakes import FakeCore, notebook_pod, notebook_service
from tests.test_notebook_lifecycle_state import T0, _Adapter, _k8s, _launch, _row, _seed
from tests.test_notebook_reconcile import FakeAdapter, _pass


@pytest.fixture
def factory(db_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    return async_sessionmaker(db_engine, expire_on_commit=False)


def _cleanup(row):
    return ((row.provider_metadata or {}).get("lifecycle") or {}).get("cleanup") or {}


# -- 1. stop and idle-stop: recorded cluster and identity before any exec or delete ---------------


async def _on_cluster(session, ns, cluster):
    ns.provider_metadata = {
        **ns.provider_metadata,
        "lifecycle": {**ns.provider_metadata["lifecycle"], "cluster": cluster},
    }
    await session.commit()


@pytest.mark.parametrize("current", ["project/region/cluster-B", None], ids=["changed", "unavailable"])
@pytest.mark.asyncio
async def test_stop_never_touches_a_different_or_unverifiable_cluster(
    session, admin_user, factory, mode_adapter_factory, monkeypatch, current
):
    from app.services import notebook_service as svc_module

    ns = await _seed(session, admin_user, status="running")
    await _on_cluster(session, ns, "project/region/cluster-A")
    core = FakeCore(pods=[notebook_pod(ns.id, ns.user_id)], services=[notebook_service(ns.id)])
    core.delete_is_immediate = True
    adapter = _k8s(mode_adapter_factory, monkeypatch, core)
    monkeypatch.setattr(adapter, "workload_cluster", AsyncMock(return_value=current))
    exec_calls = []
    monkeypatch.setattr(adapter, "_k8s_terminate_session", AsyncMock(side_effect=lambda **kw: exec_calls.append(kw)))
    monkeypatch.setattr(svc_module, "get_notebook_adapter", lambda: adapter)

    stopped = await svc_module.NotebookService.stop_session(session, ns.id, ns.user_id)
    await session.commit()

    assert stopped.status == "stopped"  # the user's stop still takes effect
    assert core.deletes() == [] and exec_calls == []
    cleanup = _cleanup(await _row(factory, ns.id))
    if current is None:
        assert cleanup["state"] == "pending" and cleanup["save_outputs"] is True
    else:
        assert cleanup["state"] == "blocked" and "cluster" in cleanup["detail"]


@pytest.mark.asyncio
async def test_idle_stop_also_checks_the_recorded_cluster(
    session, admin_user, factory, mode_adapter_factory, monkeypatch
):
    from app.adapters import registry
    from app.services import session_monitor

    ns = await _seed(session, admin_user, status="running")
    await _on_cluster(session, ns, "project/region/cluster-A")
    core = FakeCore(pods=[notebook_pod(ns.id, ns.user_id)], services=[notebook_service(ns.id)])
    adapter = _k8s(mode_adapter_factory, monkeypatch, core)
    monkeypatch.setattr(adapter, "workload_cluster", AsyncMock(return_value="project/region/cluster-B"))
    monkeypatch.setattr(registry, "get_notebook_adapter", lambda: adapter)
    monkeypatch.setattr(session_monitor, "spawn_event", lambda coro, **kw: coro.close())

    await session_monitor.SessionMonitorService._terminate_idle_session(session, ns)
    await session.commit()

    assert core.deletes() == []
    assert _cleanup(await _row(factory, ns.id))["state"] == "blocked"


@pytest.mark.asyncio
async def test_stop_refuses_conflicting_recorded_identity(
    session, admin_user, factory, mode_adapter_factory, monkeypatch
):
    from app.services import notebook_service as svc_module

    ns = await _seed(session, admin_user, status="running")
    ns.k8s_pod_name = "bioaf-notebook-another"
    await session.commit()
    core = FakeCore(pods=[notebook_pod(ns.id, ns.user_id)], services=[notebook_service(ns.id)])
    adapter = _k8s(mode_adapter_factory, monkeypatch, core)
    monkeypatch.setattr(svc_module, "get_notebook_adapter", lambda: adapter)

    await svc_module.NotebookService.stop_session(session, ns.id, ns.user_id)
    await session.commit()

    assert core.deletes() == []
    cleanup = _cleanup(await _row(factory, ns.id))
    assert cleanup["state"] == "blocked" and "conflicting" in cleanup["detail"]


# -- 2. launch completion never overwrites a committed stop ---------------------------------------


class _StopDuringLaunch(_Adapter):
    def __init__(self, session_factory, *, fail=False):
        super().__init__(session_factory, launch_error=RuntimeError("lost response") if fail else None)
        self.fail = fail

    async def launch_session(self, spec):
        async with self.factory() as db:
            await db.execute(
                update(NotebookSession)
                .where(NotebookSession.id == spec["session_id"])
                .values(status="stopped", stopped_at=datetime.now(timezone.utc))
            )
            await db.commit()
        return await super().launch_session(spec)


@pytest.mark.parametrize("fail", [False, True], ids=["completed", "errored"])
@pytest.mark.asyncio
async def test_launch_completion_never_overwrites_a_committed_stop(session, admin_user, factory, monkeypatch, fail):
    ns = await _launch(session, admin_user, _StopDuringLaunch(factory, fail=fail), monkeypatch)

    row = await _row(factory, ns.id)
    assert row.status == "stopped"
    assert row.failure_reason is None
    # Whatever the launch created after the stop is cleaned up, not left behind a finished record.
    assert _cleanup(row)["state"] == "pending"
    assert ns.status == "stopped"  # the response shows the stored state


@pytest.mark.asyncio
async def test_a_launch_that_completes_normally_still_becomes_starting(session, admin_user, factory, monkeypatch):
    ns = await _launch(session, admin_user, _Adapter(factory), monkeypatch)
    row = await _row(factory, ns.id)
    assert row.status == "starting" and ns.status == "starting"
    assert _cleanup(row) == {}


@pytest.mark.asyncio
async def test_a_refused_launch_is_recorded_failed_even_though_the_error_is_raised(
    session, admin_user, factory, monkeypatch
):
    from app.exceptions import ValidationError

    with pytest.raises(ValidationError):
        await _launch(session, admin_user, _Adapter(factory, launch_error=ValidationError("bad image")), monkeypatch)

    async with factory() as db:
        from sqlalchemy import select

        row = (await db.execute(select(NotebookSession).order_by(NotebookSession.id.desc()))).scalars().first()
    assert row.status == "failed"
    assert _cleanup(row)["state"] == "pending"


# -- 3. a failed idle-stop keeps its obligation to save outputs -----------------------------------


@pytest.mark.asyncio
async def test_an_idle_stop_retry_saves_outputs_before_deleting(session, admin_user, factory, monkeypatch):
    from app.adapters import registry
    from app.services import session_monitor

    ns = await _seed(session, admin_user, status="running")
    failing = FakeAdapter()
    failing.terminate_session = AsyncMock(side_effect=RuntimeError("temporary output sync failure"))
    monkeypatch.setattr(registry, "get_notebook_adapter", lambda: failing)
    monkeypatch.setattr(session_monitor, "spawn_event", lambda coro, **kw: coro.close())

    await session_monitor.SessionMonitorService._terminate_idle_session(session, ns)
    await session.commit()
    assert _cleanup(await _row(factory, ns.id))["save_outputs"] is True

    healthy = FakeAdapter()
    await _pass(factory, healthy, monkeypatch, now=T0 + timedelta(minutes=5))

    assert healthy.terminated and healthy.terminated[0]["ran"] is True
    assert healthy.released == []


@pytest.mark.asyncio
async def test_the_other_idle_check_also_keeps_the_obligation(session, admin_user, factory, monkeypatch):
    from app.services import notebook_service as svc_module

    ns = await _seed(session, admin_user, status="idle")
    ns.idle_since = datetime.now(timezone.utc) - timedelta(hours=5)
    await session.commit()
    failing = FakeAdapter()
    failing.terminate_session = AsyncMock(side_effect=RuntimeError("temporary failure"))
    monkeypatch.setattr(svc_module, "get_notebook_adapter", lambda: failing)
    monkeypatch.setattr(svc_module, "spawn_event", lambda coro, **kw: coro.close())

    await svc_module.NotebookService.check_idle_sessions(session, idle_timeout_hours=4)

    cleanup = _cleanup(await _row(factory, ns.id))
    assert cleanup["state"] == "pending" and cleanup["save_outputs"] is True


# -- 4. one recorded-identity rule ----------------------------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [{"k8s_pod_name": "bioaf-notebook-another"}, {"k8s_namespace": "another-namespace"}],
)
@pytest.mark.asyncio
async def test_conflicting_recorded_identity_is_never_actionable(
    session, admin_user, factory, mode_adapter_factory, monkeypatch, fields
):
    ns = await _seed(session, admin_user, status="failed")
    for key, value in fields.items():
        setattr(ns, key, value)
    await session.commit()
    core = FakeCore(pods=[notebook_pod(ns.id, ns.user_id)], services=[notebook_service(ns.id)])
    adapter = _k8s(mode_adapter_factory, monkeypatch, core)

    assert adapter.recorded_workload(ns) is None
    await _pass(factory, adapter, monkeypatch, now=T0 + timedelta(minutes=5))

    assert core.deletes() == []
    cleanup = _cleanup(await _row(factory, ns.id))
    assert cleanup["state"] == "blocked" and "conflicting" in cleanup["detail"]


@pytest.mark.asyncio
async def test_a_legacy_record_with_only_the_old_columns_is_still_cleaned_up(
    session, admin_user, factory, mode_adapter_factory, monkeypatch
):
    ns = await _seed(session, admin_user, status="failed", job_ref=None, metadata={})
    ns.k8s_pod_name = f"bioaf-notebook-{ns.id}"
    ns.k8s_namespace = "bioaf-notebooks"
    await session.commit()
    core = FakeCore(pods=[notebook_pod(ns.id, ns.user_id)], services=[notebook_service(ns.id)])
    core.delete_is_immediate = True

    await _pass(factory, _k8s(mode_adapter_factory, monkeypatch, core), monkeypatch, now=T0 + timedelta(minutes=5))

    assert not core.pods and not core.services
    assert _cleanup(await _row(factory, ns.id))["state"] == "complete"


@pytest.mark.asyncio
async def test_a_record_with_no_identity_uses_the_launch_name_and_still_verifies_the_owner(
    session, admin_user, factory, mode_adapter_factory, monkeypatch
):
    ns = await _seed(session, admin_user, status="stopped", job_ref=None, metadata={})
    core = FakeCore(pods=[notebook_pod(ns.id, user_id=ns.user_id + 1)])

    await _pass(factory, _k8s(mode_adapter_factory, monkeypatch, core), monkeypatch, now=T0 + timedelta(minutes=5))

    assert core.deletes() == []
    assert _cleanup(await _row(factory, ns.id))["state"] == "blocked"


def test_terminal_access_uses_the_same_identity_rule():
    from app.adapters.kubernetes import exec_access
    from app.services import connection_access

    assert connection_access.recorded_notebook_identity is exec_access.recorded_notebook_identity


# -- 5. provider errors do not suspend the startup deadline ---------------------------------------


@pytest.mark.asyncio
async def test_a_status_read_error_still_meets_the_startup_deadline(session, admin_user, factory, monkeypatch):
    ns = await _seed(session, admin_user)
    failing = FakeAdapter(releases=[])
    failing.get_session_status = AsyncMock(side_effect=RuntimeError("credential refresh unavailable"))
    failing.release_session_resources = AsyncMock(side_effect=RuntimeError("credential refresh unavailable"))

    await _pass(factory, failing, monkeypatch, now=T0 + timedelta(minutes=31))

    row = await _row(factory, ns.id)
    assert (row.status, row.failure_reason) == ("failed", "startup_timeout")
    assert _cleanup(row)["state"] == "pending"  # deletion waits until the provider can verify it


@pytest.mark.asyncio
async def test_a_status_read_error_within_the_deadline_changes_nothing(session, admin_user, factory, monkeypatch):
    ns = await _seed(session, admin_user)
    failing = FakeAdapter()
    failing.get_session_status = AsyncMock(side_effect=RuntimeError("credential refresh unavailable"))

    await _pass(factory, failing, monkeypatch, now=T0 + timedelta(minutes=5))

    assert (await _row(factory, ns.id)).status == "starting"


@pytest.mark.asyncio
async def test_sessions_that_keep_erroring_do_not_crowd_out_later_launches(session, admin_user, factory, monkeypatch):
    from app.services import notebook_lifecycle

    monkeypatch.setattr(notebook_lifecycle, "OBSERVE_BATCH", 2)
    stuck = [await _seed(session, admin_user) for _ in range(2)]
    later = await _seed(session, admin_user)
    seen = []

    class Flaky(FakeAdapter):
        async def get_session_status(self, session_id, **kw):
            seen.append(session_id)
            raise RuntimeError("provider unavailable")

    adapter = Flaky()
    await _pass(factory, adapter, monkeypatch, now=T0 + timedelta(minutes=5))
    await _pass(factory, adapter, monkeypatch, now=T0 + timedelta(minutes=6))

    assert later.id in seen
    assert {s.id for s in stuck} <= set(seen)
