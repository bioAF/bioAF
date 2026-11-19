"""Stopping a notebook never acts on someone else's workload and never deletes unsaved work.

Live ownership is verified before the first command runs inside the pod, not only before the
delete. If saving the session's work (home directory or outputs) fails, the pod and its Service
are kept and the stop reports that saving did not happen, so a retry can save before deleting.
A new launch must know exactly which cluster it runs on, and the startup deadline holds even
when a status read hangs.
"""

import time
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider
from app.settings_contract import ComputeMode
from tests.support.notebook_k8s_fakes import FakeCore, notebook_pod, notebook_service

POD, SVC = "bioaf-notebook-42", "bioaf-notebook-svc-42"


@pytest.fixture
def adapter(mode_adapter_factory):
    provider = mode_adapter_factory(KubernetesNotebookProvider, mode=ComputeMode.KUBERNETES)
    provider._api_client = MagicMock()
    provider._client_created_at = time.monotonic()
    return provider


def _storage():
    s = MagicMock()
    s.cli_auth_command.return_value = ""
    s.build_uri.side_effect = lambda b, k: f"s3://{b}/{k}"
    s.sync_out_command.side_effect = lambda d, prefix: ["/bin/sh", "-c", f"aws s3 sync {d} {prefix}"]
    s.cli_copy_out_file.side_effect = lambda local, uri: f"aws s3 cp {local} {uri}"
    s.list_objects = AsyncMock(return_value=[])
    return s


async def _terminate(adapter, core, *, failing=None):
    """Stop session 42 (owner 7) with home and output saving; ``failing`` picks commands that raise."""
    adapter._get_k8s_core_client = lambda: core
    execs = []

    def stream(fn, **kw):
        command = " ".join(kw.get("command") or [])
        execs.append(command)
        if failing and failing(command):
            raise RuntimeError("storage unavailable")
        return ""

    with (
        patch("app.adapters.registry.get_storage_adapter", return_value=_storage()),
        patch("kubernetes.stream.stream", side_effect=stream),
    ):
        result = await adapter._k8s_terminate_session(
            session_id=42,
            pod_name=POD,
            namespace="bioaf-notebooks",
            gcs_home_prefix="s3://bioaf-working/notebooks/42/",
            working_bucket="bioaf-working",
            session_type="jupyter",
            user_id=7,
            ran=True,
        )
    return result, execs


@pytest.mark.parametrize(
    "pod,errors,state",
    [
        (notebook_pod(42, user_id=8), {}, "blocked"),  # another owner under the recorded name
        (notebook_pod(42, 7), {("read", "pod", POD): 500}, "failed"),  # ownership cannot be read
    ],
    ids=["foreign", "unreadable"],
)
@pytest.mark.asyncio
async def test_no_command_runs_in_a_pod_whose_ownership_is_not_verified(adapter, pod, errors, state):
    core = FakeCore(pods=[pod], services=[notebook_service(42)], errors=errors)

    result, execs = await _terminate(adapter, core)

    assert execs == []
    # The pod is never deleted. A foreign pod blocks only itself: the session's own Service (its
    # labels and selector name this session) is still released; an unreadable pod defers both.
    assert [c[1] for c in core.deletes()] == (["service"] if state == "blocked" else [])
    assert result["release"]["pod"]["state"] == state


@pytest.mark.asyncio
async def test_a_pod_already_gone_is_not_exec_into_and_its_service_is_released(adapter):
    core = FakeCore(services=[notebook_service(42)])
    core.delete_is_immediate = True

    result, execs = await _terminate(adapter, core)

    assert execs == []
    assert result["release"]["pod"]["state"] == "absent" and result["release"]["service"]["state"] == "absent"


@pytest.mark.asyncio
async def test_an_owned_pod_is_saved_then_released(adapter):
    core = FakeCore(pods=[notebook_pod(42, 7)], services=[notebook_service(42)])
    core.delete_is_immediate = True

    result, execs = await _terminate(adapter, core)

    assert any("aws s3 sync /home/jovyan" in c for c in execs)
    assert any("/outputs" in c for c in execs)
    assert not result.get("save_failed")
    assert not core.pods and not core.services


@pytest.mark.parametrize(
    "which",
    [lambda c: "sync /home/jovyan" in c, lambda c: "/outputs" in c],
    ids=["home", "outputs"],
)
@pytest.mark.asyncio
async def test_a_failed_save_keeps_the_pod_and_service_for_a_retry(adapter, which):
    core = FakeCore(pods=[notebook_pod(42, 7)], services=[notebook_service(42)])
    core.delete_is_immediate = True

    result, _ = await _terminate(adapter, core, failing=which)

    assert result["save_failed"] is True
    assert core.deletes() == []
    assert POD in core.pods and SVC in core.services
    assert result["release"]["pod"]["state"] == "failed"
    assert "not saved" in result["release"]["pod"]["detail"]


@pytest.mark.asyncio
async def test_a_failed_git_commit_alone_does_not_block_release(adapter):
    """No repository (or no remote) is ordinary; it is not the session's only copy of anything."""
    core = FakeCore(pods=[notebook_pod(42, 7)], services=[notebook_service(42)])
    core.delete_is_immediate = True

    result, _ = await _terminate(adapter, core, failing=lambda c: "git add" in c)

    assert not result.get("save_failed")
    assert not core.pods


# -- the service keeps the save obligation when the adapter reports a failed save -----------------


@pytest.mark.asyncio
async def test_stop_records_a_failed_save_as_a_pending_retry_with_saving(session, admin_user, db_engine, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.adapters.models import ResourceRelease, TerminationResult, WorkloadRelease
    from app.services import notebook_service as svc_module
    from tests.test_notebook_lifecycle_state import _row, _seed
    from tests.test_notebook_reconcile import FakeAdapter

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    ns = await _seed(session, admin_user, status="running")
    adapter = FakeAdapter()
    kept = WorkloadRelease(
        pod=ResourceRelease(state="failed", detail="outputs not saved"), service=ResourceRelease(state="failed")
    )
    adapter.terminate_session = AsyncMock(
        return_value=TerminationResult(provider_details={"release": kept.model_dump(), "save_failed": True})
    )
    monkeypatch.setattr(svc_module, "get_notebook_adapter", lambda: adapter)

    await svc_module.NotebookService.stop_session(session, ns.id, ns.user_id)
    await session.commit()

    cleanup = (await _row(factory, ns.id)).provider_metadata["lifecycle"]["cleanup"]
    assert cleanup["state"] == "pending" and cleanup["save_outputs"] is True


# -- a new launch needs a complete cluster identity ------------------------------------------------


@pytest.mark.parametrize(
    "cfg",
    [
        {"gke_cluster_name": "bioaf-lab", "gcp_project_id": "lab-proj", "gke_cluster_location": "us-east1"},
        {"gke_cluster_name": "bioaf-lab", "gcp_project_id": "lab-proj"},  # location discovered
    ],
)
@pytest.mark.asyncio
async def test_the_cluster_identity_is_complete_or_none(adapter, monkeypatch, cfg):
    from types import SimpleNamespace

    from app.adapters.kubernetes import exec_access

    monkeypatch.setattr(exec_access, "backend_for_cluster_auth", lambda: "gke")
    adapter._gke.load_cluster_config = AsyncMock(return_value=dict(cfg))
    monkeypatch.setattr(
        adapter,
        "_gke_list_clusters",
        lambda parent, timeout: SimpleNamespace(
            clusters=[SimpleNamespace(name="bioaf-lab", location="us-east1")], missing_zones=[]
        ),
    )
    assert await adapter.workload_cluster() == "lab-proj/us-east1/bioaf-lab"


@pytest.mark.parametrize(
    "cfg",
    [
        {"gke_cluster_name": "bioaf-lab", "gke_cluster_location": "us-east1"},  # no project
        {"gcp_project_id": "lab-proj", "gke_cluster_location": "us-east1"},  # no cluster
        {"gke_cluster_name": "bioaf-lab", "gcp_project_id": "lab-proj"},  # location undiscoverable
    ],
)
@pytest.mark.asyncio
async def test_an_incomplete_cluster_identity_is_none(adapter, monkeypatch, cfg):
    from types import SimpleNamespace

    from app.adapters.kubernetes import exec_access

    monkeypatch.setattr(exec_access, "backend_for_cluster_auth", lambda: "gke")
    adapter._gke.load_cluster_config = AsyncMock(return_value=dict(cfg))
    monkeypatch.setattr(
        adapter, "_gke_list_clusters", lambda parent, timeout: SimpleNamespace(clusters=[], missing_zones=[])
    )
    assert await adapter.workload_cluster() is None


@pytest.mark.asyncio
async def test_eks_identity_needs_account_region_and_cluster(adapter, monkeypatch):
    from app.adapters.kubernetes import exec_access

    monkeypatch.setattr(exec_access, "backend_for_cluster_auth", lambda: "eks")
    adapter._gke.load_cluster_config = AsyncMock(
        return_value={"gke_cluster_name": "bioaf-lab", "aws_account_id": "123456789012", "aws_region": "us-east-1"}
    )
    assert await adapter.workload_cluster() == "123456789012/us-east-1/bioaf-lab"
    adapter._gke.load_cluster_config = AsyncMock(
        return_value={"gke_cluster_name": "bioaf-lab", "aws_region": "us-east-1"}
    )
    assert await adapter.workload_cluster() is None


@pytest.mark.asyncio
async def test_a_launch_without_a_known_cluster_is_refused_before_anything_is_created(
    session, admin_user, db_engine, monkeypatch
):
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.exceptions import ConflictError
    from app.models.notebook_session import NotebookSession
    from tests.test_notebook_lifecycle_state import _Adapter, _launch

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    adapter = _Adapter(factory)
    adapter.workload_cluster = AsyncMock(return_value=None)
    adapter.launch_session = AsyncMock()

    with pytest.raises(ConflictError) as exc:
        await _launch(session, admin_user, adapter, monkeypatch)
    await session.rollback()

    assert str(exc.value) == (
        "bioAF could not confirm which cluster this session would run on, so it was not started. "
        "Ask an administrator to check the compute stack."
    )
    adapter.launch_session.assert_not_awaited()
    async with factory() as db:
        assert (await db.execute(select(func.count(NotebookSession.id)))).scalar() == 0


@pytest.mark.asyncio
async def test_a_reservation_without_a_cluster_is_never_cleaned_on_the_current_one(
    session, admin_user, db_engine, monkeypatch, mode_adapter_factory
):
    """A new-style reservation with no recorded cluster (as an older build could write) is blocked."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from tests.test_notebook_lifecycle_state import T0, _k8s, _pass, _row, _seed

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    ns = await _seed(
        session, admin_user, status="failed", metadata={"lifecycle": {"reserved_at": T0.isoformat()}}
    )  # a reservation with no cluster recorded
    core = FakeCore(pods=[notebook_pod(ns.id, ns.user_id)], services=[notebook_service(ns.id)])
    adapter = _k8s(mode_adapter_factory, monkeypatch, core)
    adapter.workload_cluster = AsyncMock(return_value="lab-proj/us-east1/bioaf-lab")

    await _pass(factory, adapter, monkeypatch, T0 + timedelta(minutes=5))

    assert core.deletes() == []
    cleanup = (await _row(factory, ns.id)).provider_metadata["lifecycle"]["cleanup"]
    assert cleanup["state"] == "blocked" and "cluster" in cleanup["detail"]


# -- a hung status read still meets the deadline --------------------------------------------------


@pytest.mark.asyncio
async def test_a_hung_status_read_is_uncertainty_and_the_deadline_still_applies(
    session, admin_user, db_engine, monkeypatch
):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.services import notebook_lifecycle
    from tests.test_notebook_lifecycle_state import T0, _row, _seed
    from tests.test_notebook_reconcile import FakeAdapter, _pass

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr(notebook_lifecycle, "STATUS_READ_TIMEOUT_SECONDS", 0.05)
    ns = await _seed(session, admin_user)

    class Hung(FakeAdapter):
        async def get_session_status(self, session_id, **kw):
            await asyncio.sleep(5)

    await _pass(factory, Hung(), monkeypatch, now=T0 + timedelta(minutes=31))

    row = await _row(factory, ns.id)
    assert (row.status, row.failure_reason) == ("failed", "startup_timeout")
