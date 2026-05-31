"""Tests that the queue orchestrator is invoked from each readiness hook.

`process_queued_components` only acts when called. It must be called
after each lifecycle event that could unblock a queued component:
storage flips deployed, compute flips deployed, image build completes.
Without these hooks, the wizard's selections sit in the queue forever.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text


async def _set_config(session, key: str, value: str) -> None:
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ).bindparams(k=key, v=value)
    )


@pytest.mark.asyncio
async def test_notebook_image_poll_invokes_orchestrator_on_success(session):
    """T13: when poll_image_build sees SUCCESS, it must call
    process_queued_components so any queued notebook component can flip
    to enabled (if compute is ready) or stay provisioning.
    """
    from app.services import notebook_image_service

    await _set_config(session, "notebook_image_build_id", "build-abc")
    await _set_config(session, "notebook_image_build_status", "WORKING")
    await _set_config(session, "gcp_project_id", "demo-project")
    await _set_config(session, "gcp_region", "us-central1")
    await session.commit()

    with (
        patch.object(notebook_image_service, "check_build_status", new=AsyncMock(return_value="SUCCESS")),
        patch("app.services.component_queue.process_queued_components", new=AsyncMock()) as spy,
    ):
        await notebook_image_service.poll_image_build(session)
        await session.commit()

    assert spy.await_count == 1


@pytest.mark.asyncio
async def test_notebook_image_poll_does_not_invoke_orchestrator_on_working(session):
    """When the build is still in flight, the orchestrator should NOT be
    called -- nothing has changed about readiness.
    """
    from app.services import notebook_image_service

    await _set_config(session, "notebook_image_build_id", "build-abc")
    await _set_config(session, "notebook_image_build_status", "WORKING")
    await _set_config(session, "gcp_project_id", "demo-project")
    await session.commit()

    with (
        patch.object(notebook_image_service, "check_build_status", new=AsyncMock(return_value="WORKING")),
        patch("app.services.component_queue.process_queued_components", new=AsyncMock()) as spy,
    ):
        await notebook_image_service.poll_image_build(session)
        await session.commit()

    assert spy.await_count == 0


@pytest.mark.asyncio
async def test_cellxgene_image_poll_invokes_orchestrator_on_success(session):
    """T14: same contract for the cellxgene image poll."""
    from app.services import cellxgene_image_service

    await _set_config(session, "cellxgene_image_build_id", "build-xyz")
    await _set_config(session, "cellxgene_image_build_status", "WORKING")
    await _set_config(session, "gcp_project_id", "demo-project")
    await _set_config(session, "gcp_region", "us-central1")
    await session.commit()

    with (
        patch.object(cellxgene_image_service, "check_build_status", new=AsyncMock(return_value="SUCCESS")),
        patch("app.services.component_queue.process_queued_components", new=AsyncMock()) as spy,
    ):
        await cellxgene_image_service.poll_image_build(session)
        await session.commit()

    assert spy.await_count == 1


@pytest.mark.asyncio
async def test_stack_deployment_module_exposes_orchestrator(session):
    """T12: the stack_deployment module imports process_queued_components
    so it can call it after each readiness flip. This is a wiring smoke
    test that proves the dependency exists; the end-to-end deploy_stack
    flow is exercised via existing integration tests.
    """
    from app.services import stack_deployment

    assert hasattr(stack_deployment, "process_queued_components"), (
        "stack_deployment must import process_queued_components so it can be "
        "called after storage_deployed and compute_deployed flips."
    )
