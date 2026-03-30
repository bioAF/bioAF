"""Tests for the pipeline trigger evaluation engine."""

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.models.ingest_event import IngestEvent
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.models.pipeline_run import PipelineRun
from app.schemas.pipeline_trigger import (
    BudgetTriggerConfig,
    EventTriggerConfig,
    PipelineTriggerCreate,
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
        pipeline_key="test-trigger-pipeline",
        name="Test Pipeline",
        source_type="github",
        source_url="https://example.com",
        version="1.0",
    )
    session.add(p)
    await session.flush()
    await session.commit()
    return p


@pytest_asyncio.fixture
async def event_trigger(session, org_user, pipeline):
    org_id, user_id = org_user
    data = PipelineTriggerCreate(
        pipeline_id=pipeline.id,
        trigger_mode="event_driven",
        event_config=EventTriggerConfig(file_types=["fastq"], batching_window_minutes=0),
        budget_config=BudgetTriggerConfig(auto_queue_when_over_budget=True),
    )
    trigger = await TriggerService.create_trigger(session, org_id, user_id, data)
    await session.commit()
    return trigger


@pytest_asyncio.fixture(autouse=True)
async def clear_batches():
    TriggerService.clear_batches()
    yield
    TriggerService.clear_batches()


@pytest.mark.asyncio
async def test_create_trigger(client, admin_token, session, org_user, pipeline):
    org_id, user_id = org_user
    data = PipelineTriggerCreate(
        pipeline_id=pipeline.id,
        trigger_mode="manual",
    )
    trigger = await TriggerService.create_trigger(session, org_id, user_id, data)
    await session.commit()
    assert trigger.id is not None
    assert trigger.trigger_mode == "manual"


@pytest.mark.asyncio
async def test_disable_trigger(client, admin_token, session, org_user, event_trigger):
    _, user_id = org_user
    disabled = await TriggerService.disable_trigger(session, event_trigger.id, user_id)
    await session.commit()
    assert disabled is not None
    assert disabled.enabled is False


@pytest.mark.asyncio
async def test_event_trigger_matching_file(client, admin_token, session, org_user, event_trigger, monkeypatch):
    """Matching file triggers pipeline submission."""
    monkeypatch.setenv("BIOAF_MOCK_MONTHLY_SPEND", "0")
    monkeypatch.setenv("BIOAF_MONTHLY_BUDGET", "500.0")
    org_id, _ = org_user

    # Create a mock ingest event with a file
    from app.models.file import File

    file = File(
        organization_id=org_id,
        gcs_uri="gs://bucket/test.fastq",
        filename="test.fastq",
        file_type="fastq",
        ingest_source="auto_ingest",
    )
    session.add(file)
    await session.flush()

    ingest_event = IngestEvent(
        file_id=file.id,
        source_bucket="bioaf-ingest-demo",
        source_path="incoming/test.fastq",
        ingest_status="cataloged",
        resolved_project_id=None,
    )
    session.add(ingest_event)
    await session.flush()

    evaluations = await TriggerService.evaluate_event_triggers(ingest_event, session)
    await session.commit()

    assert len(evaluations) >= 1
    assert evaluations[0].result == "submitted"
    assert evaluations[0].pipeline_run_id is not None


@pytest.mark.asyncio
async def test_event_trigger_wrong_project(client, admin_token, session, org_user, pipeline, monkeypatch):
    """Non-matching file (wrong project) does not trigger."""
    monkeypatch.setenv("BIOAF_MOCK_MONTHLY_SPEND", "0")
    monkeypatch.setenv("BIOAF_MONTHLY_BUDGET", "500.0")
    org_id, user_id = org_user

    # Create trigger with project filter
    data = PipelineTriggerCreate(
        pipeline_id=pipeline.id,
        trigger_mode="event_driven",
        event_config=EventTriggerConfig(
            file_types=["fastq"],
            project_filter=[999],  # Non-existent project
            batching_window_minutes=0,
        ),
    )
    trigger = await TriggerService.create_trigger(session, org_id, user_id, data)
    await session.commit()

    from app.models.file import File

    file = File(
        organization_id=org_id,
        gcs_uri="gs://bucket/filtered.fastq",
        filename="filtered.fastq",
        file_type="fastq",
        ingest_source="auto_ingest",
    )
    session.add(file)
    await session.flush()

    # Create a real project to reference
    from app.models.project import Project

    project = Project(organization_id=org_id, name="FilterTestProject")
    session.add(project)
    await session.flush()

    ingest_event = IngestEvent(
        file_id=file.id,
        source_bucket="bioaf-ingest-demo",
        source_path="incoming/filtered.fastq",
        ingest_status="cataloged",
        resolved_project_id=project.id,  # Real project, not in filter [999]
    )
    session.add(ingest_event)
    await session.flush()

    evaluations = await TriggerService.evaluate_event_triggers(ingest_event, session)
    # The project-filtered trigger should not match (project_id=1 not in [999])
    # But the event_trigger fixture may still match since it has no project filter
    project_filtered_evals = [e for e in evaluations if e.trigger_id == trigger.id]
    assert len(project_filtered_evals) == 0


@pytest.mark.asyncio
async def test_batching_window(client, admin_token, session, org_user, pipeline, monkeypatch):
    """Second file within window resets timer, both files included when window expires."""
    monkeypatch.setenv("BIOAF_MOCK_MONTHLY_SPEND", "0")
    monkeypatch.setenv("BIOAF_MONTHLY_BUDGET", "500.0")
    org_id, user_id = org_user

    data = PipelineTriggerCreate(
        pipeline_id=pipeline.id,
        trigger_mode="event_driven",
        event_config=EventTriggerConfig(file_types=["fastq"], batching_window_minutes=1),
    )
    trigger = await TriggerService.create_trigger(session, org_id, user_id, data)
    await session.commit()

    from app.models.file import File

    file1 = File(
        organization_id=org_id,
        gcs_uri="gs://b/f1.fastq",
        filename="f1.fastq",
        file_type="fastq",
        ingest_source="auto_ingest",
    )
    file2 = File(
        organization_id=org_id,
        gcs_uri="gs://b/f2.fastq",
        filename="f2.fastq",
        file_type="fastq",
        ingest_source="auto_ingest",
    )
    session.add_all([file1, file2])
    await session.flush()

    ev1 = IngestEvent(
        file_id=file1.id,
        source_bucket="b",
        source_path="f1.fastq",
        ingest_status="cataloged",
    )
    ev2 = IngestEvent(
        file_id=file2.id,
        source_bucket="b",
        source_path="f2.fastq",
        ingest_status="cataloged",
    )
    session.add_all([ev1, ev2])
    await session.flush()

    await TriggerService.evaluate_event_triggers(ev1, session)
    await TriggerService.evaluate_event_triggers(ev2, session)
    await session.commit()

    # Check that both files are in the batch (keyed by (trigger_id, experiment_id))
    batches = TriggerService.get_active_batches()
    # Both events have no experiment, so key is (trigger.id, None)
    batch_key = (trigger.id, None)
    assert batch_key in batches
    assert len(batches[batch_key]["file_ids"]) == 2


@pytest.mark.asyncio
async def test_budget_queues_run(client, admin_token, session, org_user, event_trigger, monkeypatch):
    """Over budget queues the run instead of submitting."""
    monkeypatch.setenv("BIOAF_MOCK_MONTHLY_SPEND", "500.0")
    monkeypatch.setenv("BIOAF_MONTHLY_BUDGET", "500.0")
    org_id, _ = org_user

    from app.models.file import File

    file = File(
        organization_id=org_id,
        gcs_uri="gs://b/budget.fastq",
        filename="budget.fastq",
        file_type="fastq",
        ingest_source="auto_ingest",
    )
    session.add(file)
    await session.flush()

    ev = IngestEvent(
        file_id=file.id,
        source_bucket="b",
        source_path="budget.fastq",
        ingest_status="cataloged",
    )
    session.add(ev)
    await session.flush()

    evaluations = await TriggerService.evaluate_event_triggers(ev, session)
    await session.commit()

    assert len(evaluations) >= 1
    assert evaluations[0].result == "queued"
    # Verify the run status
    result = await session.execute(
        text(f"SELECT status FROM pipeline_runs WHERE id = {evaluations[0].pipeline_run_id}")
    )
    assert result.fetchone().status == "pending_budget_review"


@pytest.mark.asyncio
async def test_admin_approval(client, admin_token, session, org_user, event_trigger, monkeypatch):
    """Admin approval bypasses budget check and sets run to pending."""
    monkeypatch.setenv("BIOAF_MOCK_MONTHLY_SPEND", "500.0")
    monkeypatch.setenv("BIOAF_MONTHLY_BUDGET", "500.0")
    org_id, user_id = org_user

    # Create a queued run
    run = PipelineRun(
        organization_id=org_id,
        pipeline_name="test",
        status="pending_budget_review",
        cost_estimate=5.00,
    )
    session.add(run)
    await session.flush()
    await session.commit()

    approved = await TriggerService.approve_queued_run(run.id, user_id, session)
    await session.commit()
    assert approved is not None
    assert approved.status == "pending"


@pytest.mark.asyncio
async def test_trigger_stats(client, admin_token, session, event_trigger):
    stats = await TriggerService.get_trigger_stats(event_trigger.id, session)
    assert stats["runs_triggered_7d"] == 0
    assert stats["runs_triggered_30d"] == 0
