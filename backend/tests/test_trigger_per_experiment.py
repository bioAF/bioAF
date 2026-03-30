"""Tests for per-experiment batching and scheduled trigger execution."""

import time

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.models.file import File
from app.models.ingest_event import IngestEvent
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.models.project import Project
from app.schemas.pipeline_trigger import (
    EventTriggerConfig,
    PipelineTriggerCreate,
    ScheduleTriggerConfig,
)
from app.services.trigger_service import TriggerService


@pytest_asyncio.fixture
async def org_user(client, admin_token, session):
    result = await session.execute(text("SELECT id FROM organizations LIMIT 1"))
    org = result.fetchone()
    result = await session.execute(text("SELECT id FROM users LIMIT 1"))
    user = result.fetchone()
    return org.id, user.id


@pytest_asyncio.fixture
async def pipeline(session, org_user):
    org_id, _ = org_user
    p = PipelineCatalogEntry(
        organization_id=org_id,
        pipeline_key="per-exp-test-pipeline",
        name="Per-Experiment Test Pipeline",
        source_type="github",
        source_url="https://example.com",
        version="1.0",
    )
    session.add(p)
    await session.flush()
    await session.commit()
    return p


@pytest_asyncio.fixture
async def experiments(session, org_user):
    """Create two experiments under a project."""
    from app.models.experiment import Experiment

    org_id, _ = org_user
    project = Project(organization_id=org_id, name="BatchTestProject")
    session.add(project)
    await session.flush()

    exp_a = Experiment(name="Experiment A", project_id=project.id, organization_id=org_id)
    exp_b = Experiment(name="Experiment B", project_id=project.id, organization_id=org_id)
    session.add_all([exp_a, exp_b])
    await session.flush()
    await session.commit()
    return exp_a, exp_b


@pytest_asyncio.fixture(autouse=True)
async def clear_batches():
    TriggerService.clear_batches()
    yield
    TriggerService.clear_batches()


def _make_file_and_event(session, org_id, filename, experiment_id=None, project_id=None):
    """Helper to create a file + ingest event. Must be awaited for flush."""
    f = File(
        organization_id=org_id,
        gcs_uri=f"gs://bucket/{filename}",
        filename=filename,
        file_type="fastq",
        ingest_source="auto_ingest",
    )
    session.add(f)
    return f, experiment_id, project_id


# ---------------------------------------------------------------------------
# Per-experiment batching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batching_separates_experiments(
    client, admin_token, session, org_user, pipeline, experiments, monkeypatch
):
    """Files from different experiments get separate batch windows."""
    monkeypatch.setenv("BIOAF_MOCK_MONTHLY_SPEND", "0")
    monkeypatch.setenv("BIOAF_MONTHLY_BUDGET", "500.0")
    org_id, user_id = org_user
    exp_a, exp_b = experiments

    data = PipelineTriggerCreate(
        pipeline_id=pipeline.id,
        trigger_mode="event_driven",
        event_config=EventTriggerConfig(file_types=["fastq"], batching_window_minutes=15),
    )
    trigger = await TriggerService.create_trigger(session, org_id, user_id, data)
    await session.commit()

    # File 1 for experiment A
    f1 = File(
        organization_id=org_id,
        gcs_uri="gs://b/a1.fastq",
        filename="a1.fastq",
        file_type="fastq",
        ingest_source="auto_ingest",
    )
    # File 2 for experiment B
    f2 = File(
        organization_id=org_id,
        gcs_uri="gs://b/b1.fastq",
        filename="b1.fastq",
        file_type="fastq",
        ingest_source="auto_ingest",
    )
    session.add_all([f1, f2])
    await session.flush()

    ev1 = IngestEvent(
        file_id=f1.id,
        source_bucket="b",
        source_path="a1.fastq",
        ingest_status="cataloged",
        resolved_experiment_id=exp_a.id,
    )
    ev2 = IngestEvent(
        file_id=f2.id,
        source_bucket="b",
        source_path="b1.fastq",
        ingest_status="cataloged",
        resolved_experiment_id=exp_b.id,
    )
    session.add_all([ev1, ev2])
    await session.flush()

    await TriggerService.evaluate_event_triggers(ev1, session)
    await TriggerService.evaluate_event_triggers(ev2, session)
    await session.commit()

    batches = TriggerService.get_active_batches()
    # Should have two separate batch keys, not one
    batch_keys = [k for k in batches if k[0] == trigger.id] if isinstance(list(batches.keys())[0], tuple) else []
    assert len(batch_keys) == 2, f"Expected 2 per-experiment batches, got {len(batch_keys)}: {batches}"


@pytest.mark.asyncio
async def test_batch_expiry_per_experiment(client, admin_token, session, org_user, pipeline, experiments, monkeypatch):
    """Expired batch for one experiment doesn't affect another's window."""
    monkeypatch.setenv("BIOAF_MOCK_MONTHLY_SPEND", "0")
    monkeypatch.setenv("BIOAF_MONTHLY_BUDGET", "500.0")
    org_id, user_id = org_user
    exp_a, exp_b = experiments

    data = PipelineTriggerCreate(
        pipeline_id=pipeline.id,
        trigger_mode="event_driven",
        event_config=EventTriggerConfig(file_types=["fastq"], batching_window_minutes=1),
    )
    await TriggerService.create_trigger(session, org_id, user_id, data)
    await session.commit()

    f1 = File(
        organization_id=org_id,
        gcs_uri="gs://b/ea1.fastq",
        filename="ea1.fastq",
        file_type="fastq",
        ingest_source="auto_ingest",
    )
    f2 = File(
        organization_id=org_id,
        gcs_uri="gs://b/eb1.fastq",
        filename="eb1.fastq",
        file_type="fastq",
        ingest_source="auto_ingest",
    )
    session.add_all([f1, f2])
    await session.flush()

    # Experiment A file arrives first
    ev1 = IngestEvent(
        file_id=f1.id,
        source_bucket="b",
        source_path="ea1.fastq",
        ingest_status="cataloged",
        resolved_experiment_id=exp_a.id,
    )
    session.add(ev1)
    await session.flush()
    await TriggerService.evaluate_event_triggers(ev1, session)

    # Force expire experiment A's batch
    batches = TriggerService.get_active_batches()
    for key in batches:
        if isinstance(key, tuple) and key[1] == exp_a.id:
            batches[key]["expiry_time"] = time.time() - 1

    # Experiment B file arrives (should get its own window)
    ev2 = IngestEvent(
        file_id=f2.id,
        source_bucket="b",
        source_path="eb1.fastq",
        ingest_status="cataloged",
        resolved_experiment_id=exp_b.id,
    )
    session.add(ev2)
    await session.flush()
    await TriggerService.evaluate_event_triggers(ev2, session)

    # Process expired batches -- only exp A should fire
    evaluations = await TriggerService.process_expired_batches(session)
    await session.commit()

    assert len(evaluations) == 1, f"Expected 1 expired batch (exp A), got {len(evaluations)}"
    # Experiment B should still have an active batch
    remaining = TriggerService.get_active_batches()
    assert len(remaining) == 1, "Experiment B batch should still be active"


@pytest.mark.asyncio
async def test_files_without_experiment_batch_separately(client, admin_token, session, org_user, pipeline, monkeypatch):
    """Files with no resolved experiment get their own batch (experiment_id=None)."""
    monkeypatch.setenv("BIOAF_MOCK_MONTHLY_SPEND", "0")
    monkeypatch.setenv("BIOAF_MONTHLY_BUDGET", "500.0")
    org_id, user_id = org_user

    data = PipelineTriggerCreate(
        pipeline_id=pipeline.id,
        trigger_mode="event_driven",
        event_config=EventTriggerConfig(file_types=["fastq"], batching_window_minutes=15),
    )
    await TriggerService.create_trigger(session, org_id, user_id, data)
    await session.commit()

    f1 = File(
        organization_id=org_id,
        gcs_uri="gs://b/unmatched.fastq",
        filename="unmatched.fastq",
        file_type="fastq",
        ingest_source="auto_ingest",
    )
    session.add(f1)
    await session.flush()

    ev = IngestEvent(
        file_id=f1.id,
        source_bucket="b",
        source_path="unmatched.fastq",
        ingest_status="cataloged",
        resolved_experiment_id=None,
    )
    session.add(ev)
    await session.flush()

    await TriggerService.evaluate_event_triggers(ev, session)
    await session.commit()

    batches = TriggerService.get_active_batches()
    assert len(batches) == 1
    key = list(batches.keys())[0]
    # Key should be (trigger_id, None) for unresolved experiment
    assert isinstance(key, tuple)
    assert key[1] is None


@pytest.mark.asyncio
async def test_new_file_resets_experiment_timer(
    client, admin_token, session, org_user, pipeline, experiments, monkeypatch
):
    """A new file for the same experiment resets the batch expiry timer."""
    monkeypatch.setenv("BIOAF_MOCK_MONTHLY_SPEND", "0")
    monkeypatch.setenv("BIOAF_MONTHLY_BUDGET", "500.0")
    org_id, user_id = org_user
    exp_a, _ = experiments

    data = PipelineTriggerCreate(
        pipeline_id=pipeline.id,
        trigger_mode="event_driven",
        event_config=EventTriggerConfig(file_types=["fastq"], batching_window_minutes=15),
    )
    trigger = await TriggerService.create_trigger(session, org_id, user_id, data)
    await session.commit()

    f1 = File(
        organization_id=org_id,
        gcs_uri="gs://b/r1.fastq",
        filename="r1.fastq",
        file_type="fastq",
        ingest_source="auto_ingest",
    )
    session.add(f1)
    await session.flush()

    ev1 = IngestEvent(
        file_id=f1.id,
        source_bucket="b",
        source_path="r1.fastq",
        ingest_status="cataloged",
        resolved_experiment_id=exp_a.id,
    )
    session.add(ev1)
    await session.flush()

    await TriggerService.evaluate_event_triggers(ev1, session)
    batches = TriggerService.get_active_batches()
    first_key = (trigger.id, exp_a.id)
    first_expiry = batches[first_key]["expiry_time"]

    # Second file arrives -- timer should reset to later
    f2 = File(
        organization_id=org_id,
        gcs_uri="gs://b/r2.fastq",
        filename="r2.fastq",
        file_type="fastq",
        ingest_source="auto_ingest",
    )
    session.add(f2)
    await session.flush()

    ev2 = IngestEvent(
        file_id=f2.id,
        source_bucket="b",
        source_path="r2.fastq",
        ingest_status="cataloged",
        resolved_experiment_id=exp_a.id,
    )
    session.add(ev2)
    await session.flush()

    await TriggerService.evaluate_event_triggers(ev2, session)
    await session.commit()

    batches = TriggerService.get_active_batches()
    second_expiry = batches[first_key]["expiry_time"]
    assert second_expiry >= first_expiry
    assert len(batches[first_key]["file_ids"]) == 2


# ---------------------------------------------------------------------------
# Scheduled trigger execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduled_trigger_evaluates(client, admin_token, session, org_user, pipeline, monkeypatch):
    """Scheduled trigger finds unprocessed files and submits a run."""
    monkeypatch.setenv("BIOAF_MOCK_MONTHLY_SPEND", "0")
    monkeypatch.setenv("BIOAF_MONTHLY_BUDGET", "500.0")
    org_id, user_id = org_user

    data = PipelineTriggerCreate(
        pipeline_id=pipeline.id,
        trigger_mode="scheduled",
        schedule_config=ScheduleTriggerConfig(
            cron_expression="0 0 * * *",
            file_types=["fastq"],
            min_files_to_trigger=1,
        ),
    )
    trigger = await TriggerService.create_trigger(session, org_id, user_id, data)
    await session.commit()

    f1 = File(
        organization_id=org_id,
        gcs_uri="gs://b/sched.fastq",
        filename="sched.fastq",
        file_type="fastq",
        ingest_source="auto_ingest",
    )
    session.add(f1)
    await session.flush()
    await session.commit()

    evaluations = await TriggerService.evaluate_scheduled_triggers(session)
    await session.commit()

    assert len(evaluations) >= 1
    submitted = [e for e in evaluations if e.trigger_id == trigger.id]
    assert len(submitted) == 1
    assert submitted[0].result == "submitted"


@pytest.mark.asyncio
async def test_scheduled_trigger_skips_below_minimum(client, admin_token, session, org_user, pipeline, monkeypatch):
    """Scheduled trigger does not fire when file count is below minimum."""
    monkeypatch.setenv("BIOAF_MOCK_MONTHLY_SPEND", "0")
    monkeypatch.setenv("BIOAF_MONTHLY_BUDGET", "500.0")
    org_id, user_id = org_user

    data = PipelineTriggerCreate(
        pipeline_id=pipeline.id,
        trigger_mode="scheduled",
        schedule_config=ScheduleTriggerConfig(
            cron_expression="0 0 * * *",
            file_types=["fastq"],
            min_files_to_trigger=5,
        ),
    )
    trigger = await TriggerService.create_trigger(session, org_id, user_id, data)
    await session.commit()

    # Only one file, minimum is 5
    f1 = File(
        organization_id=org_id,
        gcs_uri="gs://b/min.fastq",
        filename="min.fastq",
        file_type="fastq",
        ingest_source="auto_ingest",
    )
    session.add(f1)
    await session.flush()
    await session.commit()

    evaluations = await TriggerService.evaluate_scheduled_triggers(session)
    await session.commit()

    our_evals = [e for e in evaluations if e.trigger_id == trigger.id]
    assert len(our_evals) == 1
    assert our_evals[0].result == "no_files"
