"""What the auto-run launcher records and announces for each launch it attempts.

A pending auto-run is marked ``launched`` only when the compute provider accepted the job. A refused
submission still produces a committed failed run (the launcher links it), but the pending entry is
``cancelled`` with reason ``launch_failed``, and AUTO_RUN_LAUNCHED is not announced. A successful
launch is announced only after the launcher's transaction commits.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.adapters.compute.kubernetes import _job_submit_result_from_dict
from app.models.experiment import Experiment
from app.models.experiment_auto_run import ExperimentAutoRun
from app.models.file import File
from app.models.pending_auto_run import PendingAutoRun
from app.models.pipeline_run import PipelineRun
from app.models.sample import Sample, sample_files
from app.services.auto_run_service import AutoRunService
from app.services.event_bus import event_bus
from app.services.event_types import AUTO_RUN_LAUNCHED
from app.services.pipeline_catalog_service import PipelineCatalogService
from app.services.post_commit_events import wait_for_deferred_events


@pytest.fixture(autouse=True)
def launched_events():
    event_bus.clear()
    seen: list[dict] = []

    async def record(payload):
        seen.append(payload)

    event_bus.subscribe(AUTO_RUN_LAUNCHED, record)
    yield seen
    event_bus.clear()


@pytest_asyncio.fixture
async def due_pending_run(session, admin_user) -> int:
    org_id = admin_user.organization_id
    await PipelineCatalogService.initialize_builtin_pipelines(session, org_id)
    experiment = Experiment(
        organization_id=org_id, name="Auto Exp", owner_user_id=admin_user.id, status="fastq_uploaded"
    )
    session.add(experiment)
    await session.flush()
    sample = Sample(experiment_id=experiment.id, external_id="A1", organism="Homo sapiens", tissue_type="PBMC")
    session.add(sample)
    await session.flush()
    for read in ("R1", "R2"):
        f = File(
            organization_id=org_id,
            experiment_id=experiment.id,
            gcs_uri=f"gs://bucket/A1_{read}_001.fastq.gz",
            filename=f"A1_{read}_001.fastq.gz",
            file_type="fastq",
        )
        session.add(f)
        await session.flush()
        await session.execute(sample_files.insert().values(sample_id=sample.id, file_id=f.id))
    config = ExperimentAutoRun(
        organization_id=org_id,
        experiment_id=experiment.id,
        pipeline_key="nf-core/scrnaseq",
        parameters_json={"aligner": "cellranger"},
        configured_by_user_id=admin_user.id,
    )
    session.add(config)
    await session.flush()
    now = datetime.now(timezone.utc)
    pending = PendingAutoRun(
        organization_id=org_id,
        auto_run_config_id=config.id,
        experiment_id=experiment.id,
        sample_id=sample.id,
        sample_completed_at=now,
        scheduled_at=now - timedelta(minutes=1),
        status="waiting",
    )
    session.add(pending)
    await session.commit()
    return pending.id


def _adapter(*, accept: bool):
    adapter = MagicMock()
    if accept:
        adapter.submit_job = AsyncMock(
            return_value=_job_submit_result_from_dict(
                {"job_id": "bioaf-auto-1", "namespace": "bioaf-pipelines", "status": "queued"}
            )
        )
    else:
        adapter.submit_job = AsyncMock(side_effect=RuntimeError("admission webhook denied the request"))
    return adapter


@pytest.mark.asyncio
async def test_a_refused_auto_run_is_not_marked_launched_and_is_not_announced(
    session, due_pending_run, launched_events
):
    with patch("app.services.pipeline_run_service.get_compute_adapter", return_value=_adapter(accept=False)):
        processed = await AutoRunService.process_pending_runs(session)
    await session.commit()
    await wait_for_deferred_events()

    assert processed == 1
    pending = await session.get(PendingAutoRun, due_pending_run)
    assert pending.status == "cancelled"
    assert pending.cancelled_reason == "launch_failed"
    run = await session.get(PipelineRun, pending.pipeline_run_id)
    assert run.status == "failed"
    assert launched_events == []


@pytest.mark.asyncio
async def test_an_accepted_auto_run_is_announced_only_after_commit(session, due_pending_run, launched_events):
    with patch("app.services.pipeline_run_service.get_compute_adapter", return_value=_adapter(accept=True)):
        await AutoRunService.process_pending_runs(session)
    await wait_for_deferred_events()
    assert launched_events == []

    await session.commit()
    await wait_for_deferred_events()

    pending = (await session.execute(select(PendingAutoRun))).scalar_one()
    assert pending.status == "launched"
    assert [e["entity_id"] for e in launched_events] == [pending.pipeline_run_id]


@pytest.mark.asyncio
async def test_an_auto_run_batch_that_rolls_back_announces_nothing(session, due_pending_run, launched_events):
    with patch("app.services.pipeline_run_service.get_compute_adapter", return_value=_adapter(accept=True)):
        await AutoRunService.process_pending_runs(session)
    await session.rollback()
    await wait_for_deferred_events()

    assert launched_events == []
