"""What a launch leaves behind when the compute provider refuses the job, and when it accepts it.

The contract (owner decisions of 2026-11-03): a provider rejection is answered with HTTP 200 and the
failed run, for catalog and custom launches alike. The failed run, its input links and its audit
entry are committed by the request, the run carries a stable ``failure_reason`` and a plain-language
``error_message`` (the provider's own text goes to the server log), the experiment does not advance,
and the failure is announced only after the commit. Every check here reads through a fresh session,
because a same-session read after ``flush()`` proves nothing about what was committed.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.compute.kubernetes import _job_submit_result_from_dict
from app.models.audit_log import AuditLog
from app.models.environment import Environment
from app.models.environment_version import EnvironmentVersion
from app.models.experiment import Experiment
from app.models.file import File
from app.models.pipeline_run import PipelineRun, PipelineRunSample
from app.models.pipeline_run_input_file import PipelineRunInputFile
from app.models.project import Project
from app.models.sample import Sample, sample_files
from app.services.event_bus import event_bus
from app.services.event_types import PIPELINE_FAILED, PIPELINE_STARTED
from app.services.post_commit_events import wait_for_deferred_events

PROVIDER_ERROR = "Kubernetes API 500: etcdserver timeout at 10.12.0.4:2379 (job bioaf-pipeline-x)"


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _clear_event_bus():
    event_bus.clear()
    yield
    event_bus.clear()


@pytest.fixture
def events():
    seen: dict[str, list[dict]] = {PIPELINE_STARTED: [], PIPELINE_FAILED: []}
    for event_type, bucket in seen.items():

        async def record(payload, bucket=bucket):
            bucket.append(payload)

        event_bus.subscribe(event_type, record)
    return seen


@pytest_asyncio.fixture
async def fresh(db_engine):
    """A session that shares nothing with the request's: it sees only committed rows."""
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s


def _rejecting_adapter():
    adapter = MagicMock()
    adapter.submit_job = AsyncMock(side_effect=RuntimeError(PROVIDER_ERROR))
    return adapter


def _accepting_adapter():
    adapter = MagicMock()
    adapter.submit_job = AsyncMock(
        return_value=_job_submit_result_from_dict(
            {"job_id": "bioaf-pipeline-ok", "namespace": "bioaf-pipelines", "status": "queued"}
        )
    )
    return adapter


@pytest_asyncio.fixture
async def experiment(session, admin_user):
    project = Project(organization_id=admin_user.organization_id, name="Failure Project", owner_user_id=admin_user.id)
    session.add(project)
    await session.flush()
    exp = Experiment(
        organization_id=admin_user.organization_id,
        project_id=project.id,
        name="Failure Experiment",
        owner_user_id=admin_user.id,
        status="fastq_uploaded",
    )
    session.add(exp)
    await session.commit()
    return exp


# --- Custom pipelines ---


@pytest_asyncio.fixture
async def custom_launch(client, session, admin_user, admin_token, experiment):
    """A launchable custom pipeline version and the request body that launches it."""
    await session.execute(
        sa_text(
            "INSERT INTO platform_config (key, value) VALUES ('results_bucket_name', 'bioaf-results-test') "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    )
    env = Environment(
        name="Failure Env",
        organization_id=admin_user.organization_id,
        created_by_user_id=admin_user.id,
        environment_type="pipeline",
    )
    session.add(env)
    await session.flush()
    env_version = EnvironmentVersion(
        environment_id=env.id,
        version_number=1,
        status="ready",
        definition_format="conda",
        definition_content="name: p\n",
        image_uri="registry.test/bioaf/p:v1",
        created_by_user_id=admin_user.id,
    )
    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://bioaf-raw-test/uploads/failure_R1.fastq.gz",
        filename="failure_R1.fastq.gz",
        file_type="fastq",
        experiment_id=experiment.id,
        source_type="upload",
    )
    session.add_all([env_version, f])
    await session.commit()

    pipeline_id = (
        await client.post("/api/v1/custom-pipelines", json={"name": "Failing Custom"}, headers=_bearer(admin_token))
    ).json()["id"]
    version_id = (
        await client.post(
            f"/api/v1/custom-pipelines/{pipeline_id}/versions",
            json={
                "code_source_type": "inline",
                "code_content": "print()",
                "entrypoint_command": "python /code/script.py",
                "environment_version_id": env_version.id,
            },
            headers=_bearer(admin_token),
        )
    ).json()["id"]
    return {
        "url": f"/api/v1/custom-pipelines/{pipeline_id}/launch",
        "body": {"version_id": version_id, "experiment_id": experiment.id, "input_file_ids": [f.id]},
        "file_id": f.id,
    }


@pytest.mark.asyncio
async def test_custom_provider_rejection_returns_and_commits_the_failed_run(
    client, admin_token, admin_user, experiment, custom_launch, fresh, events, caplog
):
    user_id = admin_user.id
    with (
        patch("app.services.custom_pipeline_service.get_compute_adapter", return_value=_rejecting_adapter()),
        caplog.at_level(logging.ERROR),
    ):
        response = await client.post(custom_launch["url"], json=custom_launch["body"], headers=_bearer(admin_token))
    await wait_for_deferred_events()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "failed"
    assert body["failure_reason"] == "submission_failed"
    assert PROVIDER_ERROR not in (body["error_message"] or "")
    assert body["error_message"]

    run = await fresh.get(PipelineRun, body["id"])
    assert run is not None
    assert run.status == "failed"
    assert run.failure_reason == "submission_failed"
    assert run.error_message == body["error_message"]
    assert run.started_at is None
    assert run.completed_at is not None
    assert run.submitted_by_user_id == user_id

    links = (
        (
            await fresh.execute(
                select(PipelineRunInputFile.file_id).where(PipelineRunInputFile.pipeline_run_id == run.id)
            )
        )
        .scalars()
        .all()
    )
    assert list(links) == [custom_launch["file_id"]]

    audit = (
        await fresh.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "pipeline_run", AuditLog.entity_id == run.id, AuditLog.action == "launch"
            )
        )
    ).scalar_one()
    assert audit.user_id == user_id
    assert audit.details_json["status"] == "failed"

    assert (await fresh.get(Experiment, experiment.id)).status == "fastq_uploaded"

    assert events[PIPELINE_STARTED] == []
    assert [e["entity_id"] for e in events[PIPELINE_FAILED]] == [run.id]
    assert PROVIDER_ERROR not in events[PIPELINE_FAILED][0]["message"]

    # Technical detail is kept, in the server log, with the run it belongs to.
    assert any(PROVIDER_ERROR in r.getMessage() and str(run.id) in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_custom_successful_submission_commits_a_running_run_and_advances_the_experiment(
    client, admin_token, experiment, custom_launch, fresh, events
):
    adapter = _accepting_adapter()
    with patch("app.services.custom_pipeline_service.get_compute_adapter", return_value=adapter):
        response = await client.post(custom_launch["url"], json=custom_launch["body"], headers=_bearer(admin_token))
    await wait_for_deferred_events()

    assert response.status_code == 200, response.text
    assert adapter.submit_job.await_count == 1
    from app.adapters.job_specs import CustomCommandJobSpec

    assert isinstance(adapter.submit_job.call_args.args[0], CustomCommandJobSpec)
    run = await fresh.get(PipelineRun, response.json()["id"])
    assert run.status == "running"
    assert run.compute_job_ref == "bioaf-pipeline-ok"
    assert run.k8s_job_name == "bioaf-pipeline-ok"
    assert run.started_at is not None
    assert run.failure_reason is None
    assert (await fresh.get(Experiment, experiment.id)).status == "processing"
    assert [e["entity_id"] for e in events[PIPELINE_STARTED]] == [run.id]
    assert events[PIPELINE_FAILED] == []


@pytest.mark.asyncio
async def test_custom_validation_rejection_creates_no_run_and_submits_nothing(
    client, admin_token, custom_launch, fresh, events
):
    adapter = _accepting_adapter()
    body = {**custom_launch["body"], "input_file_ids": [999999]}
    with patch("app.services.custom_pipeline_service.get_compute_adapter", return_value=adapter):
        response = await client.post(custom_launch["url"], json=body, headers=_bearer(admin_token))
    await wait_for_deferred_events()

    assert response.status_code == 400
    assert adapter.submit_job.await_count == 0
    assert (await fresh.execute(select(func.count()).select_from(PipelineRun))).scalar_one() == 0
    assert events[PIPELINE_STARTED] == [] and events[PIPELINE_FAILED] == []


# --- Catalog pipelines ---


@pytest_asyncio.fixture
async def catalog_launch(client, session, admin_user, admin_token, experiment):
    await client.get("/api/pipelines", headers=_bearer(admin_token))
    sample = Sample(experiment_id=experiment.id, external_id="F1", organism="Homo sapiens", tissue_type="PBMC")
    session.add(sample)
    await session.flush()
    for read in ("R1", "R2"):
        f = File(
            organization_id=admin_user.organization_id,
            experiment_id=experiment.id,
            gcs_uri=f"gs://bucket/F1_{read}_001.fastq.gz",
            filename=f"F1_{read}_001.fastq.gz",
            file_type="fastq",
        )
        session.add(f)
        await session.flush()
        await session.execute(sample_files.insert().values(sample_id=sample.id, file_id=f.id))
    await session.commit()
    return {
        "pipeline_key": "nf-core/scrnaseq",
        "experiment_id": experiment.id,
        "sample_ids": [sample.id],
        "parameters": {"aligner": "cellranger"},
    }


@pytest.mark.asyncio
async def test_catalog_provider_rejection_commits_the_failed_run_without_advancing_the_experiment(
    client, admin_token, experiment, catalog_launch, fresh, events
):
    with patch("app.services.pipeline_run_service.get_compute_adapter", return_value=_rejecting_adapter()):
        response = await client.post("/api/pipeline-runs", json=catalog_launch, headers=_bearer(admin_token))
    await wait_for_deferred_events()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "failed"
    assert body["failure_reason"] == "submission_failed"
    assert PROVIDER_ERROR not in (body["error_message"] or "")

    run = await fresh.get(PipelineRun, body["id"])
    assert run.status == "failed"
    assert run.started_at is None
    assert run.completed_at is not None
    sample_links = (
        await fresh.execute(
            select(func.count()).select_from(PipelineRunSample).where(PipelineRunSample.pipeline_run_id == run.id)
        )
    ).scalar_one()
    assert sample_links == 1
    file_links = (
        await fresh.execute(
            select(func.count()).select_from(PipelineRunInputFile).where(PipelineRunInputFile.pipeline_run_id == run.id)
        )
    ).scalar_one()
    assert file_links == 2
    audit = (
        await fresh.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "pipeline_run", AuditLog.entity_id == run.id, AuditLog.action == "launch"
            )
        )
    ).scalar_one()
    assert audit.details_json["status"] == "failed"

    assert (await fresh.get(Experiment, experiment.id)).status == "fastq_uploaded"
    experiment_audits = (
        await fresh.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.entity_type == "experiment", AuditLog.action == "status_change")
        )
    ).scalar_one()
    assert experiment_audits == 0

    assert events[PIPELINE_STARTED] == []
    assert [e["entity_id"] for e in events[PIPELINE_FAILED]] == [run.id]
    assert PROVIDER_ERROR not in events[PIPELINE_FAILED][0]["message"]


@pytest.mark.asyncio
async def test_catalog_successful_submission_advances_the_experiment_and_announces_after_commit(
    client, admin_token, experiment, catalog_launch, fresh, events
):
    adapter = _accepting_adapter()
    with patch("app.services.pipeline_run_service.get_compute_adapter", return_value=adapter):
        response = await client.post("/api/pipeline-runs", json=catalog_launch, headers=_bearer(admin_token))
    await wait_for_deferred_events()

    assert response.status_code == 200, response.text
    assert adapter.submit_job.await_count == 1
    from app.adapters.job_specs import NextflowJobSpec

    submitted = adapter.submit_job.call_args.args[0]
    assert isinstance(submitted, NextflowJobSpec)
    assert submitted.pipeline_key == "nf-core/scrnaseq"
    run = await fresh.get(PipelineRun, response.json()["id"])
    assert run.status == "running"
    assert run.compute_job_ref == "bioaf-pipeline-ok"
    assert (await fresh.get(Experiment, experiment.id)).status == "processing"
    assert [e["entity_id"] for e in events[PIPELINE_STARTED]] == [run.id]
    assert events[PIPELINE_FAILED] == []


@pytest.mark.asyncio
async def test_launch_events_are_discarded_when_the_caller_rolls_back(
    session, admin_user, experiment, catalog_launch, events
):
    """A non-HTTP caller that abandons its transaction announces nothing about the run it dropped."""
    from app.schemas.pipeline_run import PipelineRunLaunchRequest
    from app.services.pipeline_run_service import PipelineRunService

    org_id, user_id = admin_user.organization_id, admin_user.id
    for adapter in (_accepting_adapter(), _rejecting_adapter()):
        with patch("app.services.pipeline_run_service.get_compute_adapter", return_value=adapter):
            await PipelineRunService.launch_run(session, org_id, user_id, PipelineRunLaunchRequest(**catalog_launch))
        await session.rollback()
    await wait_for_deferred_events()

    assert events[PIPELINE_STARTED] == [] and events[PIPELINE_FAILED] == []


@pytest.mark.asyncio
async def test_launch_does_not_commit_the_callers_unrelated_pending_work(
    session, db_engine, admin_user, experiment, catalog_launch, fresh
):
    from app.schemas.pipeline_run import PipelineRunLaunchRequest
    from app.services.pipeline_run_service import PipelineRunService

    session.add(Project(organization_id=admin_user.organization_id, name="Uncommitted", owner_user_id=admin_user.id))
    await session.flush()
    with patch("app.services.pipeline_run_service.get_compute_adapter", return_value=_rejecting_adapter()):
        await PipelineRunService.launch_run(
            session, admin_user.organization_id, admin_user.id, PipelineRunLaunchRequest(**catalog_launch)
        )

    seen = (
        await fresh.execute(select(func.count()).select_from(Project).where(Project.name == "Uncommitted"))
    ).scalar_one()
    assert seen == 0
    await session.rollback()


# --- Quota denial (owner decision 2026-11-03) ---
#
# A denied launch answers 409, creates no run and submits nothing, but its `quota_exceeded` audit
# entry is committed in its own transaction (the request's is rolled back), and QUOTA_WARNING is
# announced only after that commit. Nothing the caller had pending is committed with it.


async def _exhausted_quota(session, user_id: int, org_id: int) -> int:
    from datetime import datetime, timedelta, timezone
    from decimal import Decimal

    from app.models.user_quota import UserQuota

    quota = UserQuota(
        user_id=user_id,
        organization_id=org_id,
        cpu_hours_monthly_limit=10,
        cpu_hours_used_current_month=Decimal("10"),
        quota_reset_at=datetime.now(timezone.utc) + timedelta(days=20),
    )
    session.add(quota)
    await session.commit()
    return quota.id


@pytest.fixture
def quota_warnings():
    from app.services.event_types import QUOTA_WARNING

    seen: list[dict] = []

    async def record(payload):
        seen.append(payload)

    event_bus.subscribe(QUOTA_WARNING, record)
    return seen


async def _quota_audits(fresh) -> list[AuditLog]:
    return list((await fresh.execute(select(AuditLog).where(AuditLog.action == "quota_exceeded"))).scalars().all())


@pytest.mark.asyncio
async def test_custom_quota_denial_commits_its_audit_and_warns_after(
    client, session, admin_user, admin_token, custom_launch, fresh, events, quota_warnings
):
    user_id = admin_user.id
    quota_id = await _exhausted_quota(session, user_id, admin_user.organization_id)
    adapter = _accepting_adapter()
    with patch("app.services.custom_pipeline_service.get_compute_adapter", return_value=adapter):
        response = await client.post(custom_launch["url"], json=custom_launch["body"], headers=_bearer(admin_token))
    await wait_for_deferred_events()

    assert response.status_code == 409
    assert adapter.submit_job.await_count == 0
    assert (await fresh.execute(select(func.count()).select_from(PipelineRun))).scalar_one() == 0
    audits = await _quota_audits(fresh)
    assert [(a.user_id, a.entity_id) for a in audits] == [(user_id, quota_id)]
    assert [w["entity_id"] for w in quota_warnings] == [quota_id]
    assert events[PIPELINE_STARTED] == [] and events[PIPELINE_FAILED] == []


@pytest.mark.asyncio
async def test_catalog_quota_denial_commits_its_audit_and_warns_after(
    client, session, admin_user, admin_token, catalog_launch, fresh, quota_warnings
):
    quota_id = await _exhausted_quota(session, admin_user.id, admin_user.organization_id)
    adapter = _accepting_adapter()
    with patch("app.services.pipeline_run_service.get_compute_adapter", return_value=adapter):
        response = await client.post("/api/pipeline-runs", json=catalog_launch, headers=_bearer(admin_token))
    await wait_for_deferred_events()

    assert response.status_code == 409
    assert adapter.submit_job.await_count == 0
    assert (await fresh.execute(select(func.count()).select_from(PipelineRun))).scalar_one() == 0
    assert [a.entity_id for a in await _quota_audits(fresh)] == [quota_id]
    assert [w["entity_id"] for w in quota_warnings] == [quota_id]


@pytest.mark.asyncio
async def test_quota_denial_does_not_commit_the_callers_unrelated_pending_work(
    session, admin_user, catalog_launch, fresh, quota_warnings
):
    from app.exceptions import ConflictError
    from app.schemas.pipeline_run import PipelineRunLaunchRequest
    from app.services.pipeline_run_service import PipelineRunService

    org_id, user_id = admin_user.organization_id, admin_user.id
    await _exhausted_quota(session, user_id, org_id)
    session.add(Project(organization_id=org_id, name="Pending Elsewhere", owner_user_id=user_id))
    await session.flush()

    with pytest.raises(ConflictError):
        await PipelineRunService.launch_run(session, org_id, user_id, PipelineRunLaunchRequest(**catalog_launch))
    await wait_for_deferred_events()

    pending = (
        await fresh.execute(select(func.count()).select_from(Project).where(Project.name == "Pending Elsewhere"))
    ).scalar_one()
    assert pending == 0
    assert len(await _quota_audits(fresh)) == 1
    assert len(quota_warnings) == 1
    await session.rollback()


@pytest.mark.asyncio
async def test_quota_denial_whose_audit_cannot_commit_still_denies_and_does_not_warn(
    session, admin_user, catalog_launch, fresh, quota_warnings
):
    from app.exceptions import ConflictError
    from app.schemas.pipeline_run import PipelineRunLaunchRequest
    from app.services.pipeline_run_service import PipelineRunService

    org_id, user_id = admin_user.organization_id, admin_user.id
    await _exhausted_quota(session, user_id, org_id)

    real_commit = AsyncSession.commit

    async def failing_commit(self):
        if self is not session:
            raise RuntimeError("database unavailable")
        return await real_commit(self)

    with patch.object(AsyncSession, "commit", failing_commit), pytest.raises(ConflictError):
        await PipelineRunService.launch_run(session, org_id, user_id, PipelineRunLaunchRequest(**catalog_launch))
    await wait_for_deferred_events()

    assert await _quota_audits(fresh) == []
    assert quota_warnings == []
    await session.rollback()


@pytest.mark.asyncio
async def test_quota_denial_keeps_the_requests_api_key_attribution(session, admin_user, catalog_launch, fresh):
    """The denial audit is written on its own session but still names the key behind the request.

    The public key scopes cannot reach a launch route today, so this holds the service boundary.
    """
    from app.exceptions import ConflictError
    from app.schemas.pipeline_run import PipelineRunLaunchRequest
    from app.services.audit_service import current_api_key_id
    from app.services.pipeline_run_service import PipelineRunService

    org_id, user_id = admin_user.organization_id, admin_user.id
    await _exhausted_quota(session, user_id, org_id)

    token = current_api_key_id.set(4242)
    try:
        with pytest.raises(ConflictError):
            await PipelineRunService.launch_run(session, org_id, user_id, PipelineRunLaunchRequest(**catalog_launch))
    finally:
        current_api_key_id.reset(token)
    await session.rollback()

    assert [(a.user_id, a.api_key_id) for a in await _quota_audits(fresh)] == [(user_id, 4242)]
