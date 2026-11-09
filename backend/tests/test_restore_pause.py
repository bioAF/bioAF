"""Automatic and external work pause around a restore review.

During a review, requests read and write the review copy by design, but automatic work must not
(it would write the review copy, or the ``postgres`` database while Accept renames), and work with
external effects must not start (cloud resources the primary would never learn about after a
reject). So, before the swap:

- automatic work pauses: periodic iterations are skipped, the Pub/Sub listener stops, and the swap
  waits for in-flight iterations, events, and detached jobs to settle;
- if they do not settle in time, the restore is refused before any swap, and work resumes;
- request-triggered external work is refused at its domain boundary until the review ends;
- a successful accept or revert resumes work; a failed revert keeps it paused, with a reminder.
"""

import asyncio
import logging

import pytest

from app import runtime
from app.runtime import OwnerPaused, TaskOwner
from app.services import backup_service
from app.services.backup_service import RestoreService
from app.tasks.periodic import PeriodicJob, run_periodic
from tests.test_restore_expiry import FILENAME, PRIMARY, _until, harness  # noqa: F401 -- fixture

pytestmark = pytest.mark.asyncio


async def test_a_paused_owner_skips_periodic_iterations_until_resumed():
    owner = TaskOwner()
    ran = []

    async def iteration():
        ran.append(owner.paused)

    job = PeriodicJob(role="poller", prepare=lambda: iteration, interval=0.01, first_delay=0, error_message="x: %s")
    with runtime.bound(owner):
        assert await owner.pause("restore_review", settle_seconds=1) == []
        task = owner.spawn(run_periodic(job), role="poller", worker=True)
        await asyncio.sleep(0.05)
        assert ran == []
        owner.resume()
        await _until(lambda: len(ran) >= 1)
    assert set(ran) == {None}
    await owner.shutdown(drain_seconds=0, cancel_seconds=1)
    assert task.done()


async def test_pausing_waits_for_an_iteration_in_progress():
    owner = TaskOwner()
    started, release = asyncio.Event(), asyncio.Event()

    async def iteration():
        started.set()
        await release.wait()

    job = PeriodicJob(role="poller", prepare=lambda: iteration, interval=3600, first_delay=0, error_message="x: %s")
    with runtime.bound(owner):
        owner.spawn(run_periodic(job), role="poller", worker=True)
    await started.wait()
    pausing = asyncio.create_task(owner.pause("restore_review", settle_seconds=2))
    await asyncio.sleep(0.05)
    assert not pausing.done()
    release.set()
    assert await pausing == []
    await owner.shutdown(drain_seconds=0, cancel_seconds=1)


async def test_pausing_reports_detached_work_that_does_not_settle():
    owner = TaskOwner()
    owner.spawn(asyncio.Event().wait(), role="stack-deploy")
    assert await owner.pause("restore_review", settle_seconds=0.1) == ["stack-deploy"]
    await owner.shutdown(drain_seconds=0, cancel_seconds=1)


async def test_external_work_is_refused_while_paused_and_admitted_after():
    owner = TaskOwner()
    with runtime.bound(owner):
        runtime.require_admission("pipeline launch")
        await owner.pause("restore_review", settle_seconds=0)
        with pytest.raises(OwnerPaused):
            runtime.require_admission("pipeline launch")
        owner.resume()
        runtime.require_admission("pipeline launch")


async def test_a_restore_is_refused_before_the_swap_when_work_does_not_settle(harness, monkeypatch):  # noqa: F811
    owner = TaskOwner()
    monkeypatch.setattr(backup_service, "RESTORE_SETTLE_SECONDS", 0.1)
    deploying = owner.spawn(asyncio.Event().wait(), role="stack-deploy")
    with runtime.bound(owner):
        result = await RestoreService.start(None, org_id=1, filename=FILENAME)

    assert result["status"] == "error"
    assert "still running" in result["message"]
    assert not any(c[0] == "swap" for c in harness.calls)
    assert ("sql", "DROP DATABASE IF EXISTS bioaf_restore") in harness.calls
    assert owner.paused is None
    assert RestoreService.get_status() == {"active": False}
    deploying.cancel()


async def test_the_review_keeps_work_paused_until_it_is_rejected(harness):  # noqa: F811
    owner = TaskOwner()
    with runtime.bound(owner):
        assert (await RestoreService.start(None, org_id=1, filename=FILENAME))["status"] == "reviewing"
        assert owner.paused == "restore_review"
        with pytest.raises(OwnerPaused):
            runtime.require_admission("pipeline launch")
        assert (await RestoreService.reject())["status"] == "reverted"
    assert owner.paused is None
    await owner.shutdown(drain_seconds=0, cancel_seconds=1)


async def test_an_accepted_review_resumes_work(harness):  # noqa: F811
    owner = TaskOwner()
    with runtime.bound(owner):
        await RestoreService.start(None, org_id=1, filename=FILENAME)
        assert (await RestoreService.accept())["status"] == "accepted"
    assert owner.paused is None
    await owner.shutdown(drain_seconds=0, cancel_seconds=1)


async def test_a_failed_revert_keeps_work_paused_and_reminds(harness, monkeypatch, caplog):  # noqa: F811
    monkeypatch.setattr(backup_service, "RESTORE_REMINDER_SECONDS", 0.02)
    owner = TaskOwner()
    with runtime.bound(owner):
        await RestoreService.start(None, org_id=1, filename=FILENAME)
        harness.fail = lambda stmt: stmt == "DROP DATABASE IF EXISTS bioaf_restore"
        with caplog.at_level(logging.WARNING, logger="bioaf.backup_service"):
            assert (await RestoreService.reject())["status"] == "error"
            await asyncio.sleep(0.1)
        assert owner.paused == "restore_review"
        reminders = [r for r in caplog.records if "still paused" in r.getMessage()]
        assert len(reminders) >= 2
        harness.fail = None
        assert (await RestoreService.reject())["status"] == "reverted"
    assert owner.paused is None
    await owner.shutdown(drain_seconds=0, cancel_seconds=1)


async def test_launching_a_pipeline_during_a_review_is_refused_before_any_write(client, admin_token, monkeypatch):
    from app.services.pipeline_catalog_service import PipelineCatalogService

    owner = runtime.current_owner()
    await owner.pause("restore_review", settle_seconds=0)
    writes = []

    async def must_not_create(*args, **kwargs):
        writes.append(True)
        raise AssertionError("launch reached the service")

    monkeypatch.setattr(PipelineCatalogService, "get_pipeline", must_not_create)
    try:
        resp = await client.post(
            "/api/pipeline-runs",
            json={"pipeline_key": "nf-core/rnaseq", "experiment_id": 1},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        readable = await client.get("/api/backups/restore/status", headers={"Authorization": f"Bearer {admin_token}"})
    finally:
        owner.resume()
    assert resp.status_code == 409
    assert "restore review" in resp.json()["detail"].lower()
    assert readable.status_code == 200
    assert writes == []


def _boundaries():
    from app.services import cellxgene_image_service, notebook_image_service
    from app.services.cellxgene_service import CellxgeneService
    from app.services.custom_pipeline_service import CustomPipelineService
    from app.services.notebook_service import NotebookService
    from app.services.pipeline_run_service import PipelineRunService
    from app.services.reference_data_service import ReferenceDataService
    from app.services.slurm_service import SlurmService
    from app.services.terraform_executor import TerraformExecutor
    from app.services.work_node_service import WorkNodeService

    return {
        "pipeline launch": PipelineRunService.launch_run,
        "custom pipeline launch": CustomPipelineService.launch_run,
        "terraform plan": TerraformExecutor.run_plan,
        "terraform destroy": TerraformExecutor.run_destroy,
        "notebook launch": NotebookService.launch_session,
        "cellxgene publish": CellxgeneService.publish_dataset,
        "work node launch": WorkNodeService.launch_work_node,
        "notebook image build": notebook_image_service.submit_image_build,
        "cellxgene image build": cellxgene_image_service.submit_image_build,
        "slurm submit": SlurmService.submit_job,
        "reference import": ReferenceDataService.start_import,
    }


@pytest.mark.parametrize("boundary", sorted(_boundaries()))
async def test_every_external_work_boundary_refuses_while_paused(boundary):
    """Refused before the boundary reads any of its inputs (placeholders stand in for them)."""
    import inspect

    fn = _boundaries()[boundary]
    params = inspect.signature(fn).parameters.values()
    args = [None for p in params if p.default is p.empty and p.kind is p.POSITIONAL_OR_KEYWORD]
    kwargs = {p.name: None for p in params if p.default is p.empty and p.kind is p.KEYWORD_ONLY}
    owner = TaskOwner()
    await owner.pause("restore_review", settle_seconds=0)
    with runtime.bound(owner):
        with pytest.raises(OwnerPaused):
            result = fn(*args, **kwargs)
            # A streamed operation (Terraform destroy) refuses on its first step.
            await (anext(result) if inspect.isasyncgen(result) else result)


# --- admission covers the operation's lifetime ------------------------


async def test_an_admitted_operation_in_progress_keeps_the_pause_waiting():
    owner = TaskOwner()
    admitted, release = asyncio.Event(), asyncio.Event()

    async def launch():
        runtime.require_admission("pipeline launch")
        admitted.set()
        await release.wait()  # submitting, writing rows

    with runtime.bound(owner):
        request = asyncio.create_task(launch())
    await admitted.wait()
    assert await owner.pause("restore_review", settle_seconds=0.1) == ["operation:pipeline launch"]
    owner.resume()
    release.set()
    await request
    assert await owner.pause("restore_review", settle_seconds=0.1) == []


async def test_an_open_admission_reservation_keeps_the_pause_waiting():
    owner = TaskOwner()
    reserved, release = asyncio.Event(), asyncio.Event()

    async def request():
        with runtime.bound(owner):
            with runtime.admission(role="hosted-review"):
                reserved.set()
                await release.wait()  # the job row is being committed

    handler = asyncio.create_task(request())
    await reserved.wait()
    assert await owner.pause("restore_review", settle_seconds=0.1) == ["reservation:hosted-review"]
    owner.resume()
    release.set()
    await handler


async def test_response_jobs_are_refused_while_paused():
    owner = TaskOwner()
    await owner.pause("restore_review", settle_seconds=0)
    with runtime.bound(owner):
        with pytest.raises(OwnerPaused):
            with runtime.admission(role="hosted-review"):
                pytest.fail("admitted while paused")
    assert owner.busy() == []


@pytest.mark.parametrize("route", ["glossary-scan", "hosted-review"])
async def test_response_job_routes_refuse_during_a_review_before_writing(client, admin_token, route, monkeypatch):
    from sqlalchemy import func, select

    import app.database as database_module
    from app.models.agent_review_job import AgentReviewJob
    from app.models.lab_glossary import LabGlossaryScanJob

    owner = runtime.current_owner()
    await owner.pause("restore_review", settle_seconds=0)
    headers = {"Authorization": f"Bearer {admin_token}"}
    try:
        if route == "glossary-scan":
            resp = await client.post(
                "/api/lab-knowledge/glossary/scan", json={"scan_type": "platform_wide"}, headers=headers
            )
        else:
            resp = await client.post(
                "/api/agent_reviews/run",
                json={"entity_type": "pipeline_run", "entity_id": 1, "selected_sub_item_ids": ["qc.metric_review"]},
                headers=headers,
            )
    finally:
        owner.resume()
    assert resp.status_code == 409, resp.text
    model = LabGlossaryScanJob if route == "glossary-scan" else AgentReviewJob
    async with database_module.async_session_factory() as s:
        assert (await s.execute(select(func.count(model.id)))).scalar() == 0


# --- listener starts respect the pause ---------------------------------------


async def test_a_listener_start_during_a_review_does_not_start_one_until_work_resumes(session):
    from unittest.mock import patch

    from app.services import pubsub_listener
    from tests.test_pubsub_listener import _seed_config
    from tests.test_pubsub_listener_lifetime import SUBSCRIPTION, _provider

    await _seed_config(session, {"auto_ingest_enabled": "true", "pubsub_subscription_name": SUBSCRIPTION})
    owner = runtime.current_owner()
    provider = _provider([], lambda: None)
    with patch("app.services.pubsub_listener.create_messaging_provider", return_value=provider) as create:
        await owner.pause("restore_review", settle_seconds=0)
        try:
            await pubsub_listener.restart_listener_if_needed()
            await asyncio.sleep(0.05)
            assert create.call_count == 0
            listener = pubsub_listener.get_listener()
            assert listener is None or not listener.running
        finally:
            owner.resume()
        await pubsub_listener.restart_listener_if_needed()
        await _until(lambda: provider.pull.await_count >= 1)
        await pubsub_listener.stop_listener()


async def test_enabling_auto_ingest_during_a_review_starts_no_listener(client, admin_token, session):
    from unittest.mock import patch

    from tests.test_pubsub_listener import _seed_config
    from tests.test_pubsub_listener_lifetime import SUBSCRIPTION, _provider

    await _seed_config(session, {"auto_ingest_enabled": "false", "pubsub_subscription_name": SUBSCRIPTION})
    owner = runtime.current_owner()
    provider = _provider([], lambda: None)
    with patch("app.services.pubsub_listener.create_messaging_provider", return_value=provider) as create:
        await owner.pause("restore_review", settle_seconds=0)
        try:
            resp = await client.post(
                "/api/v1/settings/auto-ingest",
                json={"enabled": True, "cleanup_policy": "delete_after_copy"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            await asyncio.sleep(0.05)
        finally:
            owner.resume()
    assert resp.status_code == 200
    assert create.call_count == 0 and provider.pull.await_count == 0
