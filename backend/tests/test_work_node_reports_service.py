"""Plan 03.5D: the backend polls Work Node reports, records only authenticated replies as heartbeats,
bounds its work, and never stops a node because reports are missing.

The provider adapter is mocked at ``report_vm``; its route/identity checks are proven in
test_work_node_report_adapters.py and the SSH exchange in test_work_node_report.py.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.adapters.models import NodeReportResult
from app.models.notebook_session import ComputeSession
from app.services import work_node_reports
from app.services.work_node_reports import report_round, report_status
from tests import test_work_nodes as _work_node_suite

seed_environment = _work_node_suite.seed_environment
seed_platform_config = _work_node_suite.seed_platform_config
seed_project = _work_node_suite.seed_project
seed_session_credentials = _work_node_suite.seed_session_credentials


def _factory():
    from app.database import async_session_factory

    return async_session_factory


def _node(user, **over) -> ComputeSession:
    values = dict(
        user_id=user.id,
        organization_id=user.organization_id,
        session_type="ssh",
        resource_profile="custom",
        machine_type="n2-standard-4",
        cpu_cores=4,
        memory_gb=16,
        status="running",
        gce_instance_name="bioaf-worknode-1",
        gce_zone="us-central1-b",
        gce_project_id="proj",
        provider_instance_id="555",
        launch_attempt_id="attempt-1",
        sync_private_key="sync-private",
        host_public_key="ssh-ed25519 AAAAhost",
        node_trust_version=1,
        sync_route="private-v1",
        provider_metadata={"launch_journal": {"requests": []}},
    )
    values.update(over)
    return ComputeSession(**values)


@pytest_asyncio.fixture
async def node(session, comp_bio_user):
    n = _node(comp_bio_user)
    session.add(n)
    await session.commit()
    return n


def _adapter(result=None, side_effect=None):
    adapter = MagicMock(name="work node adapter")
    adapter.report_vm = AsyncMock(return_value=result, side_effect=side_effect)
    adapter.terminate_vm = AsyncMock()
    return adapter


async def _round(adapter, **kwargs):
    with patch("app.services.work_node_reports.get_work_node_adapter", return_value=adapter):
        return await report_round(_factory(), **kwargs)


async def _row(node_id) -> ComputeSession:
    async with _factory()() as s:
        return (await s.execute(select(ComputeSession).where(ComputeSession.id == node_id))).scalar_one()


@pytest.mark.asyncio
async def test_an_authenticated_report_is_the_heartbeat_and_carries_the_policy(node):
    before = datetime.now(timezone.utc)
    adapter = _adapter(NodeReportResult(outcome="reported", ssh_policy_status="failed_closed"))
    await _round(adapter)

    kwargs = adapter.report_vm.call_args.kwargs
    assert adapter.report_vm.call_args.args[:2] == ("bioaf-worknode-1", "us-central1-b")
    assert kwargs["sync_private_key"] == "sync-private"
    assert kwargs["host_public_key"] == "ssh-ed25519 AAAAhost"
    assert kwargs["provider_instance_id"] == "555"
    assert kwargs["launch_attempt_id"] == "attempt-1"
    assert kwargs["sync_route"] == "private-v1"
    assert kwargs["session_id"] == node.id

    row = await _row(node.id)
    assert row.heartbeat_at is not None and row.heartbeat_at >= before
    assert row.report_attempted_at is not None
    assert row.report_failure is None
    assert row.provider_metadata["ssh_policy_status"] == "failed_closed"
    # Unrelated provider metadata is preserved.
    assert row.provider_metadata["launch_journal"] == {"requests": []}
    assert report_status(row, datetime.now(timezone.utc)) == "current"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["unreachable", "untrusted", "malformed", "mismatch", "unavailable"])
async def test_a_failed_attempt_never_moves_the_last_heartbeat(session, node, outcome):
    earlier = datetime.now(timezone.utc) - timedelta(hours=2)
    node.heartbeat_at = earlier
    node.provider_metadata = {"ssh_policy_status": "applied"}
    await session.commit()

    await _round(_adapter(NodeReportResult(outcome=outcome)))

    row = await _row(node.id)
    assert row.heartbeat_at == earlier
    assert row.report_failure == outcome
    assert row.report_attempted_at is not None
    assert row.provider_metadata == {"ssh_policy_status": "applied"}
    assert report_status(row, datetime.now(timezone.utc)) == "unreachable"


@pytest.mark.asyncio
async def test_an_unknown_policy_in_a_report_does_not_erase_the_known_one(session, node):
    node.provider_metadata = {"ssh_policy_status": "applied"}
    await session.commit()
    await _round(_adapter(NodeReportResult(outcome="reported", ssh_policy_status=None)))
    row = await _row(node.id)
    assert row.heartbeat_at is not None
    assert row.provider_metadata["ssh_policy_status"] == "applied"


@pytest.mark.asyncio
async def test_missing_reports_never_stop_a_node(session, node):
    """Owner decision (2026-11-25): no automatic stopping, whatever reporting does."""
    from app.services.work_node_service import WorkNodeService

    node.started_at = datetime.now(timezone.utc) - timedelta(days=30)
    node.heartbeat_at = None
    await session.commit()
    adapter = _adapter(NodeReportResult(outcome="unreachable"))
    for _ in range(3):
        async with _factory()() as s:
            await s.execute(
                text("UPDATE compute_sessions SET report_attempted_at = NULL WHERE id = :id"), {"id": node.id}
            )
            await s.commit()
        await _round(adapter)
        with patch("app.services.work_node_service.get_work_node_adapter", return_value=adapter):
            async with _factory()() as s:
                await WorkNodeService.check_heartbeat_timeouts(s, idle_timeout_hours=1)
    row = await _row(node.id)
    assert row.status == "running"
    adapter.terminate_vm.assert_not_called()


@pytest.mark.asyncio
async def test_a_node_without_durable_trust_is_never_contacted(session, comp_bio_user):
    legacy = _node(comp_bio_user, node_trust_version=None, sync_private_key=None, host_public_key=None)
    session.add(legacy)
    await session.commit()
    adapter = _adapter(NodeReportResult(outcome="reported"))
    await _round(adapter)
    adapter.report_vm.assert_not_called()
    row = await _row(legacy.id)
    assert report_status(row, datetime.now(timezone.utc)) == "unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["starting", "stopping", "stopped", "failed"])
async def test_only_running_or_kept_nodes_are_contacted(session, comp_bio_user, status):
    other = _node(comp_bio_user, status=status)
    session.add(other)
    await session.commit()
    adapter = _adapter(NodeReportResult(outcome="reported"))
    await _round(adapter)
    adapter.report_vm.assert_not_called()


@pytest.mark.asyncio
async def test_a_kept_node_is_still_reported(session, comp_bio_user):
    kept = _node(comp_bio_user, status="stop_failed", stop_failure="save_failed")
    session.add(kept)
    await session.commit()
    await _round(_adapter(NodeReportResult(outcome="reported")))
    assert (await _row(kept.id)).heartbeat_at is not None


@pytest.mark.asyncio
async def test_a_node_attempted_within_the_interval_is_not_contacted_again(node):
    adapter = _adapter(NodeReportResult(outcome="reported"))
    await _round(adapter)
    await _round(adapter)
    assert adapter.report_vm.await_count == 1


@pytest.mark.asyncio
async def test_concurrent_rounds_in_two_workers_contact_each_node_once(session, comp_bio_user, node):
    second = _node(comp_bio_user, gce_instance_name="bioaf-worknode-2", launch_attempt_id="attempt-2")
    session.add(second)
    await session.commit()

    async def slow(*args, **kwargs):
        await asyncio.sleep(0.05)
        return NodeReportResult(outcome="reported")

    adapter = _adapter(side_effect=slow)
    with patch("app.services.work_node_reports.get_work_node_adapter", return_value=adapter):
        await asyncio.gather(report_round(_factory()), report_round(_factory()))
    names = sorted(c.args[0] for c in adapter.report_vm.call_args_list)
    assert names == ["bioaf-worknode-1", "bioaf-worknode-2"]


@pytest.mark.asyncio
async def test_least_recently_attempted_nodes_go_first(session, comp_bio_user):
    now = datetime.now(timezone.utc)
    nodes = [
        _node(comp_bio_user, gce_instance_name="recent", report_attempted_at=now - timedelta(minutes=6)),
        _node(comp_bio_user, gce_instance_name="never"),
        _node(comp_bio_user, gce_instance_name="oldest", report_attempted_at=now - timedelta(hours=3)),
    ]
    session.add_all(nodes)
    await session.commit()
    adapter = _adapter(NodeReportResult(outcome="reported"))
    await _round(adapter, concurrency=1)
    assert [c.args[0] for c in adapter.report_vm.call_args_list] == ["never", "oldest", "recent"]


@pytest.mark.asyncio
async def test_a_hung_node_is_cut_off_at_its_deadline_and_others_still_report(session, comp_bio_user, node):
    healthy = _node(comp_bio_user, gce_instance_name="healthy", launch_attempt_id="attempt-2")
    session.add(healthy)
    await session.commit()

    async def report(name, zone, **kwargs):
        if name == "bioaf-worknode-1":
            await asyncio.sleep(30)
        return NodeReportResult(outcome="reported")

    started = asyncio.get_running_loop().time()
    await _round(_adapter(side_effect=report), node_deadline=0.2)
    assert asyncio.get_running_loop().time() - started < 5
    hung = await _row(node.id)
    assert (hung.heartbeat_at, hung.report_failure) == (None, "unreachable")
    assert (await _row(healthy.id)).heartbeat_at is not None


@pytest.mark.asyncio
async def test_the_round_budget_bounds_the_whole_pass(session, comp_bio_user):
    session.add_all([_node(comp_bio_user, gce_instance_name=f"n{i}", launch_attempt_id=f"a{i}") for i in range(4)])
    await session.commit()

    async def hang(*args, **kwargs):
        await asyncio.sleep(30)

    started = asyncio.get_running_loop().time()
    await _round(_adapter(side_effect=hang), concurrency=2, node_deadline=20, round_budget=0.3)
    assert asyncio.get_running_loop().time() - started < 5
    async with _factory()() as s:
        rows = (await s.execute(select(ComputeSession).where(ComputeSession.session_type == "ssh"))).scalars().all()
    # Nodes that were contacted are recorded as unreachable; none has a heartbeat.
    assert all(r.heartbeat_at is None for r in rows)
    assert {r.report_failure for r in rows if r.report_attempted_at is not None} == {"unreachable"}


@pytest.mark.asyncio
async def test_a_late_reply_does_not_revive_a_node_stopped_meanwhile(node):
    async def report(*args, **kwargs):
        async with _factory()() as s:
            await s.execute(
                text("UPDATE compute_sessions SET status = 'stopped', sync_private_key = NULL WHERE id = :id"),
                {"id": node.id},
            )
            await s.commit()
        return NodeReportResult(outcome="reported", ssh_policy_status="applied")

    await _round(_adapter(side_effect=report))
    row = await _row(node.id)
    assert row.status == "stopped"
    assert row.heartbeat_at is None
    assert "ssh_policy_status" not in (row.provider_metadata or {})


@pytest.mark.asyncio
async def test_a_reply_for_a_replaced_launch_is_discarded(node):
    async def report(*args, **kwargs):
        async with _factory()() as s:
            await s.execute(
                text("UPDATE compute_sessions SET launch_attempt_id = 'attempt-9' WHERE id = :id"), {"id": node.id}
            )
            await s.commit()
        return NodeReportResult(outcome="reported")

    await _round(_adapter(side_effect=report))
    assert (await _row(node.id)).heartbeat_at is None


@pytest.mark.asyncio
async def test_a_metadata_change_during_the_report_is_kept(node):
    async def report(*args, **kwargs):
        async with _factory()() as s:
            row = (await s.execute(select(ComputeSession).where(ComputeSession.id == node.id))).scalar_one()
            row.provider_metadata = {**(row.provider_metadata or {}), "written_meanwhile": True}
            await s.commit()
        return NodeReportResult(outcome="reported", ssh_policy_status="applied")

    await _round(_adapter(side_effect=report))
    meta = (await _row(node.id)).provider_metadata
    assert meta["written_meanwhile"] is True
    assert meta["ssh_policy_status"] == "applied"


@pytest.mark.asyncio
async def test_no_row_lock_is_held_while_the_node_is_contacted(node):
    async def report(*args, **kwargs):
        async with _factory()() as s:
            # NOWAIT raises if any transaction holds the row lock.
            await s.execute(text("SELECT id FROM compute_sessions WHERE id = :id FOR UPDATE NOWAIT"), {"id": node.id})
            await s.rollback()
        return NodeReportResult(outcome="reported")

    await _round(_adapter(side_effect=report))
    assert (await _row(node.id)).heartbeat_at is not None


@pytest.mark.asyncio
async def test_an_adapter_exception_is_unavailable_not_a_crash(node):
    await _round(_adapter(side_effect=RuntimeError("sdk exploded")))
    assert (await _row(node.id)).report_failure == "unavailable"


def test_report_status_wording_inputs():
    now = datetime.now(timezone.utc)
    trusted = dict(node_trust_version=1, status="running", report_failure=None)
    mk = lambda **o: MagicMock(**{**trusted, **o})  # noqa: E731
    assert report_status(mk(heartbeat_at=None), now) == "awaiting"
    assert report_status(mk(heartbeat_at=now - timedelta(minutes=2)), now) == "current"
    assert report_status(mk(heartbeat_at=now - timedelta(hours=1)), now) == "stale"
    assert report_status(mk(heartbeat_at=now - timedelta(hours=1), report_failure="unreachable"), now) == "unreachable"
    assert report_status(mk(heartbeat_at=None, report_failure="mismatch"), now) == "unreachable"
    assert report_status(mk(status="stopped", heartbeat_at=None), now) is None
    assert report_status(mk(node_trust_version=None, heartbeat_at=None), now) == "unavailable"


def test_the_round_is_bounded_by_the_plan_defaults():
    assert work_node_reports.REPORT_CONCURRENCY == 8
    assert work_node_reports.NODE_DEADLINE_SECONDS == 15
    assert work_node_reports.ROUND_BUDGET_SECONDS == 120
    assert work_node_reports.REPORT_INTERVAL == timedelta(minutes=5)


@pytest.mark.asyncio
async def test_the_existing_heartbeat_job_runs_stop_cleanup_then_a_report_round():
    """Plan 03.5D reuses the work-node-heartbeat job; no job is added and its timing is unchanged."""
    from app.tasks import jobs

    job = next(j for j in jobs.PERIODIC_JOBS if j.role == "work-node-heartbeat")
    calls = []
    with (
        patch(
            "app.services.work_node_service.WorkNodeService.check_heartbeat_timeouts",
            AsyncMock(side_effect=lambda *a, **k: calls.append("cleanup")),
        ),
        patch(
            "app.services.work_node_reports.report_round",
            AsyncMock(side_effect=lambda *a, **k: calls.append("reports")),
        ),
    ):
        await job.prepare()()
    assert calls == ["cleanup", "reports"]


@pytest.mark.asyncio
async def test_detail_and_list_show_how_reporting_stands(client, session, comp_bio_token, comp_bio_user):
    now = datetime.now(timezone.utc)
    current = _node(comp_bio_user, gce_instance_name="c", heartbeat_at=now - timedelta(minutes=1))
    lost = _node(
        comp_bio_user, gce_instance_name="l", heartbeat_at=now - timedelta(hours=2), report_failure="unreachable"
    )
    legacy = _node(comp_bio_user, gce_instance_name="o", node_trust_version=None)
    done = _node(comp_bio_user, gce_instance_name="d", status="stopped")
    session.add_all([current, lost, legacy, done])
    await session.commit()
    expected = {current.id: "current", lost.id: "unreachable", legacy.id: "unavailable", done.id: None}

    headers = {"Authorization": f"Bearer {comp_bio_token}"}
    listed = (await client.get("/api/v1/work-nodes/sessions", headers=headers)).json()["sessions"]
    assert {s["id"]: s["report_status"] for s in listed} == expected
    detail = (await client.get(f"/api/v1/work-nodes/sessions/{lost.id}", headers=headers)).json()
    assert detail["report_status"] == "unreachable"
    # The response never exposes the node's credentials or the failure internals.
    assert "sync_private_key" not in detail and "report_failure" not in detail


@pytest.mark.asyncio
async def test_reporting_needs_no_node_to_backend_callback_address(
    client,
    comp_bio_token,
    comp_bio_user,
    seed_environment,
    seed_project,
    seed_session_credentials,
    seed_platform_config,
):
    """Normal users configure no callback address; the launched node is not asked to call the backend."""
    from app.adapters.models import ServiceState, VmInfo
    from app.adapters.work_nodes.gce import _build_startup_script

    adapter = MagicMock()
    adapter.launch_vm = AsyncMock(
        return_value=VmInfo(instance_name="bioaf-worknode-1", zone="us-central1-b", status=ServiceState.STARTING)
    )
    with patch("app.services.work_node_service.get_work_node_adapter", return_value=adapter):
        response = await client.post(
            "/api/v1/work-nodes/sessions",
            json={
                "project_id": seed_project.id,
                "environment_version_id": seed_environment["ready_version"].id,
                "machine_type": "n2-standard-4",
            },
            headers={"Authorization": f"Bearer {comp_bio_token}"},
        )
    assert response.status_code == 200
    vm_spec = adapter.launch_vm.call_args.args[0]
    assert not vm_spec.get("api_base_url")
    script = _build_startup_script(vm_spec)
    assert "crontab" not in script
    assert "/heartbeat" not in script
