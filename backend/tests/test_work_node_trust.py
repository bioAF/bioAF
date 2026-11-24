"""Plan 03A service behavior: durable node credentials at launch and truthful Stop.

The adapter is mocked; the real SSH verification is proven in test_work_node_ssh_trust.py and the
provider deletion paths in the adapter suites.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.adapters.models import ServiceState, StoredObject, TerminationResult, VmInfo
from app.models.notebook_session import ComputeSession
from app.services.event_bus import event_bus
from app.services.event_types import WORK_NODE_STOPPED
from tests import test_work_nodes as _work_node_suite

# Shared fixtures from the Work Node API suite, registered here by name.
seed_environment = _work_node_suite.seed_environment
seed_platform_config = _work_node_suite.seed_platform_config
seed_project = _work_node_suite.seed_project
seed_session_credentials = _work_node_suite.seed_session_credentials


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _launch(client, token, seed_project, seed_environment):
    return await client.post(
        "/api/v1/work-nodes/sessions",
        json={
            "project_id": seed_project.id,
            "environment_version_id": seed_environment["ready_version"].id,
            "machine_type": "n2-standard-4",
        },
        headers=_bearer(token),
    )


@pytest.mark.asyncio
async def test_launch_commits_encrypted_sync_key_and_host_identity_before_the_provider_call(
    client,
    session,
    comp_bio_token,
    comp_bio_user,
    seed_environment,
    seed_project,
    seed_session_credentials,
    seed_platform_config,
):
    from app.database import async_session_factory

    seen: dict = {}

    async def fake_launch(vm_spec):
        # Read the row on a different connection: the credentials must already be committed.
        async with async_session_factory() as other:
            row = (
                await other.execute(select(ComputeSession).where(ComputeSession.id == vm_spec["session_id"]))
            ).scalar_one()
            raw = (
                await other.execute(
                    text("SELECT sync_private_key FROM compute_sessions WHERE id = :id"),
                    {"id": vm_spec["session_id"]},
                )
            ).scalar_one()
        seen.update(row=row, raw=raw, spec=dict(vm_spec))
        return VmInfo(
            instance_name="bioaf-worknode-x",
            zone="us-central1-b",
            status=ServiceState.STARTING,
            provider_details={"gcp_project_id": "proj", "provider_instance_id": "1234567890123"},
        )

    adapter = MagicMock()
    adapter.launch_vm = AsyncMock(side_effect=fake_launch)
    with patch("app.services.work_node_service.get_work_node_adapter", return_value=adapter):
        response = await _launch(client, comp_bio_token, seed_project, seed_environment)
    assert response.status_code == 200

    row, spec = seen["row"], seen["spec"]
    assert row.sync_private_key and "PRIVATE KEY" in row.sync_private_key
    assert seen["raw"].startswith("gAAAA")
    assert row.host_public_key and row.host_public_key.startswith("ssh-ed25519 ")
    assert row.launch_attempt_id
    assert row.node_trust_version == 1

    # The node receives the public sync key and its host key; never the sync private key.
    assert spec["sync_public_key"].startswith("ssh-ed25519 ")
    assert spec["host_public_key"] == row.host_public_key
    assert "PRIVATE KEY" in spec["host_private_key"]
    assert spec["launch_attempt_id"] == row.launch_attempt_id
    assert row.sync_private_key not in str(spec)

    # The immutable provider id is bound once the provider answers.
    session.expire_all()
    final = (
        await session.execute(select(ComputeSession).where(ComputeSession.id == response.json()["id"]))
    ).scalar_one()
    assert final.provider_instance_id == "1234567890123"

    body = response.text
    assert "PRIVATE KEY" not in body
    assert "sync_private_key" not in body


# -- Truthful Stop -------------------------------------------------------------------------


@pytest_asyncio.fixture
async def durable_node(session, comp_bio_user):
    node = ComputeSession(
        user_id=comp_bio_user.id,
        organization_id=comp_bio_user.organization_id,
        session_type="ssh",
        resource_profile="custom",
        machine_type="n2-standard-4",
        cpu_cores=4,
        memory_gb=16,
        status="running",
        access_url="ssh://34.1.2.3:22",
        gce_instance_name="bioaf-worknode-1",
        gce_zone="us-central1-b",
        gce_project_id="proj",
        provider_instance_id="555",
        launch_attempt_id="attempt-1",
        sync_private_key="sync-private",
        host_public_key="ssh-ed25519 AAAAhost",
        node_trust_version=1,
        heartbeat_token="hb",
        heartbeat_at=datetime.now(timezone.utc),
    )
    session.add(node)
    await session.commit()
    return node


@pytest.fixture
def adapter():
    adapter = MagicMock(name="work node adapter")
    adapter.terminate_vm = AsyncMock(return_value=TerminationResult())
    with (
        patch("app.services.work_node_service.get_work_node_adapter", return_value=adapter),
        patch.object(event_bus, "emit", AsyncMock()) as emit,
    ):
        adapter.emit = emit
        yield adapter


def _stopped_events(adapter, node_id):
    return [
        c for c in adapter.emit.call_args_list if c.args[0] == WORK_NODE_STOPPED and c.args[1]["entity_id"] == node_id
    ]


async def _row(session, node_id) -> ComputeSession:
    session.expire_all()
    return (await session.execute(select(ComputeSession).where(ComputeSession.id == node_id))).scalar_one()


@pytest.mark.asyncio
async def test_stop_hands_the_durable_credentials_to_the_provider(
    client, session, comp_bio_token, durable_node, adapter
):
    response = await client.post(f"/api/v1/work-nodes/sessions/{durable_node.id}/stop", headers=_bearer(comp_bio_token))
    assert response.status_code == 200
    kwargs = adapter.terminate_vm.call_args.kwargs
    assert kwargs["sync_private_key"] == "sync-private"
    assert kwargs["host_public_key"] == "ssh-ed25519 AAAAhost"
    assert kwargs["provider_instance_id"] == "555"
    assert kwargs["launch_attempt_id"] == "attempt-1"
    row = await _row(session, durable_node.id)
    assert row.status == "stopped"
    assert row.stopped_at is not None
    # The secret is cleared only once saving and deletion are confirmed.
    assert row.sync_private_key is None
    assert len(_stopped_events(adapter, durable_node.id)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["save_failed", "delete_unconfirmed", "state_unknown"])
async def test_a_kept_node_is_reported_stop_failed_and_keeps_its_obligations(
    client, session, comp_bio_token, durable_node, adapter, reason
):
    adapter.terminate_vm.return_value = TerminationResult(status=ServiceState.RUNNING, stop_failure=reason)
    response = await client.post(f"/api/v1/work-nodes/sessions/{durable_node.id}/stop", headers=_bearer(comp_bio_token))
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "stop_failed"
    assert body["stop_failure"] == reason
    row = await _row(session, durable_node.id)
    assert row.status == "stop_failed"
    assert row.stop_failure == reason
    assert row.stopped_at is None
    assert row.sync_private_key == "sync-private"
    assert row.stop_attempts == 1
    assert _stopped_events(adapter, durable_node.id) == []


@pytest.mark.asyncio
async def test_a_provider_exception_is_an_unknown_state_not_a_stop(
    client, session, comp_bio_token, durable_node, adapter
):
    adapter.terminate_vm.side_effect = RuntimeError("boom")
    response = await client.post(f"/api/v1/work-nodes/sessions/{durable_node.id}/stop", headers=_bearer(comp_bio_token))
    assert response.status_code == 200
    row = await _row(session, durable_node.id)
    assert (row.status, row.stop_failure, row.stopped_at) == ("stop_failed", "state_unknown", None)
    assert "boom" not in response.text


@pytest.mark.asyncio
async def test_retry_stop_after_a_failure_completes_and_registers_outputs_once(
    client, session, comp_bio_token, durable_node, adapter
):
    adapter.terminate_vm.return_value = TerminationResult(status=ServiceState.RUNNING, stop_failure="save_failed")
    await client.post(f"/api/v1/work-nodes/sessions/{durable_node.id}/stop", headers=_bearer(comp_bio_token))
    adapter.terminate_vm.return_value = TerminationResult(
        output_files=[StoredObject(filename="r.csv", storage_uri="gs://w/sessions/1/outputs/r.csv", size_bytes=3)],
        output_prefix="gs://w/sessions/1/outputs/",
    )
    response = await client.post(f"/api/v1/work-nodes/sessions/{durable_node.id}/stop", headers=_bearer(comp_bio_token))
    assert response.json()["status"] == "stopped"
    row = await _row(session, durable_node.id)
    assert row.stop_failure is None
    assert row.sync_private_key is None


@pytest.mark.asyncio
async def test_a_concurrent_stop_does_not_run_twice(client, session, comp_bio_token, durable_node, adapter):
    durable_node.status = "stopping"
    durable_node.stop_claimed_at = datetime.now(timezone.utc)
    await session.commit()
    response = await client.post(f"/api/v1/work-nodes/sessions/{durable_node.id}/stop", headers=_bearer(comp_bio_token))
    assert response.status_code == 409
    adapter.terminate_vm.assert_not_called()


@pytest.mark.asyncio
async def test_an_abandoned_stop_claim_can_be_retried(client, session, comp_bio_token, durable_node, adapter):
    durable_node.status = "stopping"
    durable_node.stop_claimed_at = datetime.now(timezone.utc) - timedelta(hours=1)
    await session.commit()
    response = await client.post(f"/api/v1/work-nodes/sessions/{durable_node.id}/stop", headers=_bearer(comp_bio_token))
    assert response.status_code == 200
    adapter.terminate_vm.assert_called_once()


@pytest.mark.asyncio
async def test_idle_cleanup_keeps_an_unsaved_node_and_retries_a_bounded_number_of_times(session, durable_node, adapter):
    from app.services.work_node_service import WorkNodeService

    durable_node.heartbeat_at = datetime.now(timezone.utc) - timedelta(hours=30)
    await session.commit()
    adapter.terminate_vm.return_value = TerminationResult(status=ServiceState.RUNNING, stop_failure="save_failed")

    for _ in range(5):
        await WorkNodeService.check_heartbeat_timeouts(session, idle_timeout_hours=24)

    row = await _row(session, durable_node.id)
    assert row.status == "stop_failed"
    assert row.stopped_at is None
    assert row.sync_private_key == "sync-private"
    assert adapter.terminate_vm.call_count == 3
    assert adapter.terminate_vm.call_args.kwargs["working_bucket"] == ""
    assert _stopped_events(adapter, durable_node.id) == []


@pytest.mark.asyncio
async def test_a_kept_node_still_counts_against_the_limit_and_accepts_heartbeats(
    client,
    session,
    comp_bio_token,
    comp_bio_user,
    durable_node,
    seed_environment,
    seed_project,
    seed_session_credentials,
    seed_platform_config,
):
    from app.services.work_node_service import WorkNodeService

    durable_node.status = "stop_failed"
    durable_node.stop_failure = "save_failed"
    second = ComputeSession(
        user_id=comp_bio_user.id,
        organization_id=comp_bio_user.organization_id,
        session_type="ssh",
        resource_profile="custom",
        cpu_cores=4,
        memory_gb=16,
        status="stop_failed",
    )
    session.add(second)
    await session.commit()

    assert await WorkNodeService.record_heartbeat(session, durable_node.id, "hb") is True
    response = await _launch(client, comp_bio_token, seed_project, seed_environment)
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_the_notebook_idle_monitor_never_stops_a_work_node(session, durable_node):
    """Work Nodes have their own heartbeat cleanup; the notebook monitor must not mark one stopped."""
    from app.services.session_monitor import SessionMonitorService

    durable_node.started_at = datetime.now(timezone.utc) - timedelta(hours=10)
    await session.commit()
    notebook_adapter = MagicMock()
    with patch("app.adapters.registry.get_notebook_adapter", return_value=notebook_adapter):
        await SessionMonitorService.poll_notebook_sessions(session, idle_timeout_hours=4)
    row = await _row(session, durable_node.id)
    assert (row.status, row.stopped_at) == ("running", None)
