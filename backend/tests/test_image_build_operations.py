"""Infrastructure image builds hold exclusion until the provider settles them (storage lifecycle 11D).

Submission is not completion: a build is admitted like any infrastructure action, keeps every
other action out while it runs, and settles only on the provider's terminal status. A build started
inside another operation belongs to it, and that operation waits for it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models.infrastructure_operation import InfrastructureOperation
from app.services import infrastructure_operations as ops


async def _op(session, op_id):
    session.expire_all()
    return (
        await session.execute(select(InfrastructureOperation).where(InfrastructureOperation.id == op_id))
    ).scalar_one()


@pytest.mark.asyncio
async def test_a_standalone_build_holds_exclusion_until_the_provider_settles_it(session, admin_user):
    user_id = admin_user.id
    op_id, created = await ops.begin_image_build("notebook", user_id)
    await ops.record_image_build(op_id, "notebook", "build-1")

    assert created is True
    op = await _op(session, op_id)
    assert (op.action, op.status, op.exclusive_slot) == ("notebook_image_build", "running", 1)
    with pytest.raises(ops.InfrastructureBusyError):
        await ops.accept(action="compute_teardown", principal=ops.Principal(user_id))

    await ops.settle_external_build("build-1", "WORKING")
    assert (await _op(session, op_id)).exclusive_slot == 1

    await ops.settle_external_build("build-1", "SUCCESS")
    op = await _op(session, op_id)
    assert (op.status, op.exclusive_slot, op.message) == ("succeeded", None, "Image build finished.")
    assert next(s for s in op.steps if s["key"] == "build:build-1")["state"] == "done"


@pytest.mark.asyncio
async def test_a_failed_build_fails_its_operation(session, admin_user):
    op_id, _ = await ops.begin_image_build("cellxgene", admin_user.id)
    await ops.record_image_build(op_id, "cellxgene", "build-2")
    await ops.settle_external_build("build-2", "FAILURE")
    op = await _op(session, op_id)
    assert (op.status, op.message, op.exclusive_slot) == ("failed", "Cellxgene image build failed.", None)


@pytest.mark.asyncio
async def test_a_build_cannot_start_while_another_operation_runs(session, admin_user):
    await ops.accept(action="compute_teardown", principal=ops.Principal(admin_user.id))
    with pytest.raises(ops.InfrastructureBusyError):
        await ops.begin_image_build("notebook", admin_user.id)


@pytest.mark.asyncio
async def test_a_nested_build_keeps_its_parent_operation_until_it_settles(session, admin_user):
    op_id, _ = await ops.accept(action="compute_deploy", principal=ops.Principal(admin_user.id))

    async def body(handle):
        build_op, created = await ops.begin_image_build("notebook", admin_user.id)
        assert (build_op, created) == (op_id, False)
        await ops.record_image_build(build_op, "notebook", "build-3")
        return ops.succeeded("Stack deployed")

    await ops.run_operation(op_id, body)

    op = await _op(session, op_id)
    assert (op.status, op.exclusive_slot, op.phase) == ("running", 1, "Waiting for image builds to finish")

    await ops.settle_external_build("build-3", "SUCCESS")
    op = await _op(session, op_id)
    assert (op.status, op.message, op.exclusive_slot) == ("succeeded", "Stack deployed", None)


@pytest.mark.asyncio
async def test_the_build_poller_settles_the_operation(session, admin_user):
    from app.services import notebook_image_service

    op_id, _ = await ops.begin_image_build("notebook", admin_user.id)
    await ops.record_image_build(op_id, "notebook", "build-4")
    await notebook_image_service._set_config(session, "notebook_image_build_id", "build-4")
    await notebook_image_service._set_config(session, "notebook_image_build_status", "WORKING")
    await session.commit()

    platform = AsyncMock()
    platform.has_target = True
    platform.config = {"region": "us-central1", "project_id": "p"}
    with (
        patch.object(notebook_image_service, "resolve_image_platform", AsyncMock(return_value=platform)),
        patch.object(notebook_image_service, "check_build_status", AsyncMock(return_value="SUCCESS")),
        patch.object(notebook_image_service, "_ensure_default_environment", AsyncMock()),
        patch("app.services.component_queue.process_queued_components", AsyncMock()),
    ):
        await notebook_image_service.poll_image_build(session)
    await session.commit()

    assert (await _op(session, op_id)).status == "succeeded"


@pytest.mark.asyncio
async def test_a_failed_submission_releases_its_own_operation(session, admin_user, monkeypatch):
    from app.services import notebook_image_service

    platform = AsyncMock()
    platform.cloud_provider = "gcp"
    platform.build_sa = "sa@x"
    platform.config = {"region": "us-central1", "project_id": "p"}
    await notebook_image_service._set_config(session, "working_bucket_name", "bioaf-working-x")
    await session.commit()

    provider = AsyncMock()
    provider.submit_build = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("quota"))
    with (
        patch.object(notebook_image_service, "_upload_build_context", AsyncMock(return_value="ctx.tgz")),
        patch.object(notebook_image_service, "resolve_image_credentials", AsyncMock(return_value=object())),
        patch.object(notebook_image_service, "get_image_build_provider", return_value=provider),
        pytest.raises(RuntimeError),
    ):
        await notebook_image_service.submit_image_build(session, platform, admin_user.id)

    latest = (
        (await session.execute(select(InfrastructureOperation).order_by(InfrastructureOperation.id.desc())))
        .scalars()
        .first()
    )
    assert (latest.status, latest.exclusive_slot) == ("failed", None)
