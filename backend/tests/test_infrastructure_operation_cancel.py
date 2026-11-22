"""Cancelling an infrastructure operation (storage lifecycle 11D.1; approved M16).

Cancellation stops remaining work at the next safe point and never undoes finished work. The
request is durable and idempotent, is reported to every admin, and keeps exclusion until stopping
is confirmed; after two minutes without confirmation the operation needs recovery.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select, text

from app.exceptions import StateError
from app.models.file import File
from app.models.infrastructure_operation import InfrastructureOperation
from app.services import infrastructure_operations as ops
from app.services import stack_deployment as stack
from app.services.storage_identity import StoreHealth, StoreReport
from app.adapters.storage.gcs import GcsStorageProvider
from app.platform.platform_config_service import PlatformConfigService
from app.services import storage_write_admission as admission
from tests.support.blocking import BlockingCall
from app.services.terraform_stream import INTERRUPTED_EVENT, TerraformProgressEvent


async def _op(session, op_id):
    session.expire_all()
    return (
        await session.execute(select(InfrastructureOperation).where(InfrastructureOperation.id == op_id))
    ).scalar_one()


async def _settled(session, op_id, timeout=5.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        op = await _op(session, op_id)
        if op.exclusive_slot is None or op.status == "recovery_required":
            return op
        assert loop.time() < deadline, f"not settled: {op.status}"
        await asyncio.sleep(0.02)


async def _running_body(session, user_id, body):
    op_id, _ = await ops.accept(action="compute_deploy", principal=ops.Principal(user_id))
    ops.spawn_operation(op_id, body, role="test-operation")
    for _ in range(100):
        if (await _op(session, op_id)).status == "running":
            break
        await asyncio.sleep(0.01)
    return op_id


@pytest.mark.asyncio
async def test_cancel_stops_the_running_work_and_releases_exclusion(session, admin_user):
    user_id = admin_user.id
    reached = asyncio.Event()

    async def body(handle):
        await handle.step("compute", "Creating compute resources")
        reached.set()
        await asyncio.sleep(30)
        return ops.succeeded()

    op_id = await _running_body(session, user_id, body)
    await reached.wait()
    with patch.object(ops, "_cancel_watchdog", AsyncMock()):
        await ops.request_cancel(session, op_id, user_id)
    op = await _settled(session, op_id)

    assert (op.status, op.exclusive_slot) == ("cancelled", None)
    assert op.message == "Cancelled. Work already done was not undone."
    assert op.cancel_requested_by_user_id == user_id
    step = next(s for s in op.steps if s["key"] == "compute")
    assert step["state"] == "stopped"


@pytest.mark.asyncio
async def test_a_stream_that_absorbs_the_cancel_still_records_cancelled(session, admin_user):
    """run_apply turns cancellation into an interrupted event; the user's request makes it cancelled."""
    user_id = admin_user.id
    gate = asyncio.Event()

    async def events(_session):
        yield TerraformProgressEvent(event_type="progress", message="Deploying compute infrastructure...")
        try:
            await gate.wait()
        except asyncio.CancelledError:
            yield TerraformProgressEvent(event_type=INTERRUPTED_EVENT)
            return

    op_id = await _running_body(session, user_id, lambda h: ops.drive_stack(h, events))
    await asyncio.sleep(0.1)
    with patch.object(ops, "_cancel_watchdog", AsyncMock()):
        await ops.request_cancel(session, op_id, user_id)
    op = await _settled(session, op_id)
    assert op.status == "cancelled"


@pytest.mark.asyncio
async def test_a_repeated_request_keeps_the_first_deadline(session, admin_user):
    user_id = admin_user.id
    op_id, _ = await ops.accept(action="storage_destroy", principal=ops.Principal(user_id))
    await session.execute(text("UPDATE infrastructure_operations SET status='running' WHERE id=:i").bindparams(i=op_id))
    await session.commit()

    with patch.object(ops, "_signal_cancel", AsyncMock()) as signal:
        await ops.request_cancel(session, op_id, user_id)
        first = (await _op(session, op_id)).cancel_deadline
        await ops.request_cancel(session, op_id, user_id)
        again = await _op(session, op_id)

    assert again.cancel_deadline == first
    assert again.status == "stopping"
    assert signal.await_count == 1
    assert first - again.cancel_requested_at == timedelta(seconds=120)
    audits = (
        await session.execute(
            text("SELECT count(*) FROM audit_log WHERE action='infrastructure_operation_cancel_requested'")
        )
    ).scalar_one()
    assert audits == 1


@pytest.mark.asyncio
async def test_a_finished_operation_keeps_its_outcome(session, admin_user):
    user_id = admin_user.id
    op_id, _ = await ops.accept(action="compute_deploy", principal=ops.Principal(user_id))
    await ops.finish(op_id, ops.succeeded("Stack deployed"))
    with pytest.raises(StateError, match="already finished"):
        await ops.request_cancel(session, op_id, user_id)
    op = await _op(session, op_id)
    assert (op.status, op.cancel_requested_at) == ("succeeded", None)


@pytest.mark.asyncio
async def test_a_late_finish_cannot_overwrite_a_settled_outcome(session, admin_user):
    user_id = admin_user.id
    op_id, _ = await ops.accept(action="compute_deploy", principal=ops.Principal(user_id))
    await ops.finish(op_id, ops.succeeded("Stack deployed"))
    await ops.finish(op_id, ops.OperationResult("interrupted", "late", "interrupted"))
    assert (await _op(session, op_id)).status == "succeeded"


@pytest.mark.asyncio
async def test_recovery_required_is_not_cancellable(session, admin_user):
    user_id = admin_user.id
    op_id, _ = await ops.accept(action="compute_deploy", principal=ops.Principal(user_id))
    await session.execute(
        text("UPDATE infrastructure_operations SET status='recovery_required' WHERE id=:i").bindparams(i=op_id)
    )
    await session.commit()
    with pytest.raises(StateError, match="needs recovery"):
        await ops.request_cancel(session, op_id, user_id)


@pytest.mark.asyncio
async def test_no_confirmation_by_the_deadline_requires_recovery_and_keeps_exclusion(session, admin_user):
    user_id = admin_user.id
    op_id, _ = await ops.accept(action="storage_destroy", principal=ops.Principal(user_id))
    await session.execute(
        text(
            "UPDATE infrastructure_operations SET status='stopping', cancel_requested_at=now() - interval '3 minutes', "
            "cancel_deadline=now() - interval '1 minute' WHERE id=:i"
        ).bindparams(i=op_id)
    )
    await session.commit()

    assert await ops.expire_cancellation(op_id) is True

    op = await _op(session, op_id)
    assert (op.status, op.exclusive_slot, op.message) == (
        "recovery_required",
        1,
        "bioAF asked Terraform to stop, but could not confirm that it did.",
    )


@pytest.mark.asyncio
async def test_the_deadline_does_nothing_before_it_passes(session, admin_user):
    user_id = admin_user.id
    op_id, _ = await ops.accept(action="storage_destroy", principal=ops.Principal(user_id))
    await session.execute(
        text(
            "UPDATE infrastructure_operations SET status='stopping', cancel_requested_at=now(), "
            "cancel_deadline=now() + interval '2 minutes' WHERE id=:i"
        ).bindparams(i=op_id)
    )
    await session.commit()
    assert await ops.expire_cancellation(op_id) is False
    assert (await _op(session, op_id)).status == "stopping"


@pytest.mark.asyncio
async def test_a_live_terraform_child_is_never_released(session, admin_user):
    user_id = admin_user.id
    op_id, _ = await ops.accept(action="compute_teardown", principal=ops.Principal(user_id))
    with (
        patch("app.services.terraform_executor.live_child_pids", return_value=[4182]),
        patch.object(ops, "pid_alive", return_value=True),
    ):
        await ops.finish(op_id, ops.OperationResult("interrupted", "stopped", "interrupted"))
    op = await _op(session, op_id)
    assert (op.status, op.exclusive_slot) == ("recovery_required", 1)


@pytest.mark.asyncio
async def test_cancelling_a_build_asks_the_provider_and_waits_for_its_confirmation(session, admin_user):
    user_id = admin_user.id
    op_id, _ = await ops.begin_image_build("notebook", user_id)
    await ops.record_image_build(op_id, "notebook", "build-9")

    with (
        patch.object(ops, "_cancel_cloud_build", AsyncMock()) as provider,
        patch.object(ops, "_cancel_watchdog", AsyncMock()),
    ):
        await ops.request_cancel(session, op_id, user_id)
    provider.assert_awaited_once_with("notebook", "build-9")
    assert (await _op(session, op_id)).exclusive_slot == 1  # a request is not settlement

    await ops.settle_external_build("build-9", "CANCELLED")
    op = await _op(session, op_id)
    assert (op.status, op.exclusive_slot) == ("cancelled", None)


@pytest.mark.asyncio
async def test_a_cancelled_operation_that_restarted_with_its_container_is_cancelled(session, admin_user):
    user_id = admin_user.id
    op_id, _ = await ops.accept(action="compute_deploy", principal=ops.Principal(user_id))
    runner = {"container_id": "c1", "container_start": "b:1", "pid": 9}
    await session.execute(
        text(
            "UPDATE infrastructure_operations SET status='stopping', cancel_requested_at=now(), "
            "cancel_deadline=now() + interval '2 minutes', runner=CAST(:r AS jsonb) WHERE id=:i"
        ).bindparams(r=json.dumps(runner), i=op_id)
    )
    await session.commit()
    with patch.object(ops, "current_runner", return_value={**runner, "container_start": "b:2"}):
        await ops.reconcile_on_startup(session)
    await session.commit()
    op = await _op(session, op_id)
    assert (op.status, op.exclusive_slot) == ("cancelled", None)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_cancel_route_needs_configure(client, admin_user, admin_token, comp_bio_token, session):
    user_id = admin_user.id
    op_id, _ = await ops.accept(action="compute_deploy", principal=ops.Principal(user_id))
    await session.execute(text("UPDATE infrastructure_operations SET status='running' WHERE id=:i").bindparams(i=op_id))
    await session.commit()
    path = f"/api/v1/infrastructure/operations/{op_id}/cancel"

    assert (await client.post(path, headers=_auth(comp_bio_token))).status_code == 403
    with patch.object(ops, "_signal_cancel", AsyncMock()):
        resp = await client.post(path, headers=_auth(admin_token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "stopping"
    assert body["cancel_requested_by"] is not None


@pytest.mark.asyncio
async def test_notebook_cancel_no_longer_claims_cancelled_before_the_provider_confirms(session, admin_user):
    from app.services import notebook_image_service

    await notebook_image_service._set_config(session, "notebook_image_build_id", "build-5")
    await notebook_image_service._set_config(session, "notebook_image_build_status", "WORKING")
    await session.commit()
    with patch.object(notebook_image_service, "request_provider_cancel", AsyncMock()) as provider:
        await notebook_image_service.cancel_build(session, admin_user.id)
    provider.assert_awaited_once()
    assert await notebook_image_service._read_config(session, "notebook_image_build_status") == "WORKING"


@pytest.mark.asyncio
async def test_a_cancel_committed_before_the_worker_starts_prevents_the_work(session, admin_user):
    """There is no task to signal yet, so the worker itself must honor the recorded request."""
    op_id, _ = await ops.accept(action="storage_destroy", principal=ops.Principal(admin_user.id))
    with patch.object(ops, "_signal_cancel", AsyncMock()):
        await ops.request_cancel(session, op_id, admin_user.id)
    effects = []

    async def body(handle):
        effects.append("deleted objects")
        return ops.succeeded()

    await ops.run_operation(op_id, body)

    assert effects == []
    session.expire_all()
    op = await _op(session, op_id)
    assert (op.status, op.exclusive_slot) == ("cancelled", None)
    assert op.message == "Cancelled. Work already done was not undone."


@pytest.mark.asyncio
async def test_request_scoped_work_cancelled_before_it_starts_does_not_run(session, admin_user):
    op_id, _ = await ops.accept(action="compute_deploy", principal=ops.Principal(admin_user.id))
    with patch.object(ops, "_signal_cancel", AsyncMock()):
        await ops.request_cancel(session, op_id, admin_user.id)
    ran = []

    with pytest.raises(ops.CancelledBeforeStart):
        async with ops.owned(op_id):
            ran.append(True)

    assert ran == []
    session.expire_all()
    assert (await _op(session, op_id)).status == "cancelled"


@pytest.mark.asyncio
async def test_cancelled_destroy_keeps_exclusion_while_delete_thread_is_still_running(session, admin_user):
    from app.services import stack_deployment as stack
    from app.services.storage_identity import StoreHealth, StoreReport

    user_id, org_id = admin_user.id, admin_user.organization_id
    for key, value in {
        "compute_deployed": "false",
        "storage_deployed": "true",
        "raw_bucket_name": "review-raw",
    }.items():
        await PlatformConfigService.set(session, key, value)
    await session.commit()
    adapter = GcsStorageProvider.__new__(GcsStorageProvider)
    call = BlockingCall()
    operation_id, _ = await ops.accept(action="storage_destroy", principal=ops.Principal(user_id))

    async def body(handle):
        return await ops.drive_stack(handle, lambda s: stack.destroy_storage(s, user_id, org_id=org_id))

    with (
        patch("app.adapters.registry.get_storage_adapter", return_value=adapter),
        patch.object(GcsStorageProvider, "is_local", property(lambda self: False)),
        patch.object(
            adapter,
            "list_objects",
            AsyncMock(
                return_value=[
                    SimpleNamespace(storage_uri="gs://review-raw/a", provider_details={"generation": 1}),
                ]
            ),
        ),
        patch.object(adapter, "_get_credentials", AsyncMock(return_value=None)),
        patch.object(adapter, "_gcs_delete", side_effect=call),
        patch(
            "app.services.storage_identity.inspect_bucket",
            AsyncMock(return_value=StoreReport("raw", "review-raw", StoreHealth.AVAILABLE)),
        ),
        patch.object(admission, "drain", AsyncMock(return_value=[])),
    ):
        task = asyncio.create_task(ops.run_operation(operation_id, body))
        try:
            await call.wait_until_started()
            await ops.request_cancel(session, operation_id, user_id)
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not call.finished.is_set()
            session.expire_all()
            operation = await session.get(InfrastructureOperation, operation_id)
            assert operation.exclusive_slot == 1, (
                f"Destroy was marked {operation.status} and released exclusion while its delete call was still running"
            )
            # Recovery is refused while the call runs; once it stops, the outcome is recorded.
            with pytest.raises(StateError):
                await ops.recover(session, operation_id, user_id, reason="checked")
            await session.rollback()
        finally:
            await call.stop()
        for _ in range(50):
            session.expire_all()
            operation = await session.get(InfrastructureOperation, operation_id)
            if operation.exclusive_slot is None:
                break
            await asyncio.sleep(0.05)
        assert (operation.status, operation.exclusive_slot) == ("cancelled", None)


@pytest.mark.asyncio
async def test_a_deletion_that_finishes_after_cancel_is_recorded_before_the_operation_settles(session, admin_user):
    """Deleting an object and recording its byte loss finish together, even when cancelled between."""
    user_id, org_id = admin_user.id, admin_user.organization_id
    for key, value in {
        "compute_deployed": "false",
        "storage_deployed": "true",
        "raw_bucket_name": "review-raw",
    }.items():
        await PlatformConfigService.set(session, key, value)
    file = File(
        organization_id=org_id,
        filename="a",
        file_type="txt",
        storage_uri="gs://review-raw/a",
        gcs_uri="gs://review-raw/a",
    )
    session.add(file)
    await session.commit()
    file_id = file.id
    adapter = GcsStorageProvider.__new__(GcsStorageProvider)
    call = BlockingCall()
    operation_id, _ = await ops.accept(action="storage_destroy", principal=ops.Principal(user_id))

    async def body(handle):
        return await ops.drive_stack(handle, lambda s: stack.destroy_storage(s, user_id, org_id=org_id))

    with (
        patch("app.adapters.registry.get_storage_adapter", return_value=adapter),
        patch.object(GcsStorageProvider, "is_local", property(lambda self: False)),
        patch.object(
            adapter,
            "list_objects",
            AsyncMock(
                return_value=[
                    SimpleNamespace(storage_uri="gs://review-raw/a", provider_details={"generation": 1}),
                ]
            ),
        ),
        patch.object(adapter, "_get_credentials", AsyncMock(return_value=None)),
        patch.object(adapter, "_gcs_delete", side_effect=call),
        patch(
            "app.services.storage_identity.inspect_bucket",
            AsyncMock(return_value=StoreReport("raw", "review-raw", StoreHealth.AVAILABLE)),
        ),
        patch.object(admission, "drain", AsyncMock(return_value=[])),
    ):
        task = asyncio.create_task(ops.run_operation(operation_id, body))
        try:
            await call.wait_until_started()
            await ops.request_cancel(session, operation_id, user_id)
            with pytest.raises(asyncio.CancelledError):
                await task
            session.expire_all()
            operation = await session.get(InfrastructureOperation, operation_id)
            assert operation.exclusive_slot == 1
        finally:
            # The provider then confirms the object was deleted successfully.
            await call.stop()
        for _ in range(100):
            session.expire_all()
            operation = await session.get(InfrastructureOperation, operation_id)
            if operation.exclusive_slot is None:
                break
            await asyncio.sleep(0.02)
        assert (operation.status, operation.exclusive_slot) == ("cancelled", None)
        session.expire_all()
        stored = await session.get(File, file_id)
        assert stored.storage_deleted, (
            "Deletion succeeded after cancellation, but the settled operation left the File claiming its bytes were not deleted"
        )
