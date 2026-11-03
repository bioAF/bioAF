"""Reproducing a pipeline run: Organization boundary, identity, and launch path.

Reproduction relaunches a stored run. The run to reproduce is chosen by ID from the URL, so the
Organization boundary has to hold before anything is reconstructed or launched: a run in another
Organization is indistinguishable from a missing one, and nothing is created, submitted, audited,
or announced for it.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.adapters.compute.kubernetes import _job_submit_result_from_dict
from app.exceptions import NotFoundError
from app.models.audit_log import AuditLog
from app.models.experiment import Experiment
from app.models.file import File
from app.models.organization import Organization
from app.models.pipeline_run import PipelineRun, PipelineRunSample
from app.models.sample import Sample, sample_files
from app.models.user import User
from app.services.bootstrap_roles import seed_builtin_roles
from app.services.event_bus import event_bus
from app.services.event_types import PIPELINE_STARTED
from app.services.pipeline_catalog_service import PipelineCatalogService
from app.services.pipeline_run_service import PipelineRunService


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _clear_event_bus():
    event_bus.clear()
    yield
    event_bus.clear()


@pytest.fixture
def started_events():
    seen: list[dict] = []

    async def record(payload):
        seen.append(payload)

    event_bus.subscribe(PIPELINE_STARTED, record)
    return seen


@pytest.fixture
def compute():
    """A compute adapter that accepts every job and records what it was given."""
    adapter = MagicMock()
    adapter.submit_job = AsyncMock(
        return_value=_job_submit_result_from_dict(
            {"job_id": "bioaf-pipeline-repro", "namespace": "bioaf-pipelines", "status": "queued"}
        )
    )
    with patch("app.services.pipeline_run_service.get_compute_adapter", return_value=adapter):
        yield adapter


async def _launchable_run(session, org_id: int, user_id: int, *, key: str = "nf-core/scrnaseq") -> PipelineRun:
    """A completed catalog run whose samples still have their reads: reproducible as it stands."""
    await PipelineCatalogService.initialize_builtin_pipelines(session, org_id)
    experiment = Experiment(organization_id=org_id, name="Repro Exp", owner_user_id=user_id, status="fastq_uploaded")
    session.add(experiment)
    await session.flush()
    sample = Sample(experiment_id=experiment.id, external_id="S1", organism="Homo sapiens", tissue_type="PBMC")
    session.add(sample)
    await session.flush()
    for read in ("R1", "R2"):
        f = File(
            organization_id=org_id,
            experiment_id=experiment.id,
            gcs_uri=f"gs://bucket/{org_id}/S1_{read}_001.fastq.gz",
            filename=f"S1_{read}_001.fastq.gz",
            file_type="fastq",
        )
        session.add(f)
        await session.flush()
        await session.execute(sample_files.insert().values(sample_id=sample.id, file_id=f.id))
    pipeline = await PipelineCatalogService.get_pipeline(session, org_id, key)
    run = PipelineRun(
        organization_id=org_id,
        experiment_id=experiment.id,
        submitted_by_user_id=user_id,
        pipeline_name=key,
        pipeline_version=pipeline.version,
        parameters_json={"aligner": "cellranger"},
        status="completed",
    )
    session.add(run)
    await session.flush()
    session.add(PipelineRunSample(pipeline_run_id=run.id, sample_id=sample.id))
    await session.commit()
    return run


@pytest_asyncio.fixture
async def other_org(session) -> SimpleNamespace:
    organization = Organization(name="Other Org", setup_complete=True)
    session.add(organization)
    await session.flush()
    roles = await seed_builtin_roles(session, organization.id)
    user = User(
        email="owner@other.test",
        password_hash="x",
        role_id=roles["admin"],
        organization_id=organization.id,
        status="active",
    )
    session.add(user)
    await session.commit()
    return SimpleNamespace(id=organization.id, user=user)


async def _count(session, model, *where) -> int:
    return (await session.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


# --- Organization boundary ---


@pytest.mark.asyncio
async def test_reproducing_another_organizations_run_is_not_found_and_has_no_effects(
    client, session, admin_user, admin_token, other_org, compute, started_events
):
    foreign = await _launchable_run(session, other_org.id, other_org.user.id)
    runs_before = await _count(session, PipelineRun)

    response = await client.post(f"/api/pipeline-runs/{foreign.id}/reproduce", headers=_bearer(admin_token))

    assert response.status_code == 404
    session.expire_all()
    assert await _count(session, PipelineRun) == runs_before
    assert compute.submit_job.await_count == 0
    assert await _count(session, AuditLog, AuditLog.action.in_(["reproduce", "launch"])) == 0
    assert started_events == []


@pytest.mark.asyncio
async def test_reproducing_a_missing_run_is_not_found(client, admin_token, compute):
    response = await client.post("/api/pipeline-runs/999999/reproduce", headers=_bearer(admin_token))

    assert response.status_code == 404
    assert compute.submit_job.await_count == 0


@pytest.mark.asyncio
async def test_reproducing_a_run_in_the_callers_organization_launches_it(
    client, session, admin_user, admin_token, compute
):
    org_id = admin_user.organization_id
    original = await _launchable_run(session, org_id, admin_user.id)

    with patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock):
        response = await client.post(f"/api/pipeline-runs/{original.id}/reproduce", headers=_bearer(admin_token))

    assert response.status_code == 200
    body = response.json()
    assert body["id"] != original.id
    assert body["resume_from_run_id"] == original.id
    assert compute.submit_job.await_count == 1
    session.expire_all()
    new_run = await session.get(PipelineRun, body["id"])
    assert new_run.organization_id == org_id
    assert await _count(session, AuditLog, AuditLog.action == "reproduce", AuditLog.entity_id == body["id"]) == 1


@pytest.mark.asyncio
async def test_reproduce_service_refuses_a_run_outside_the_stated_organization(session, admin_user, other_org, compute):
    """Non-HTTP callers cannot skip the boundary: the service takes the Organization and enforces it."""
    foreign = await _launchable_run(session, other_org.id, other_org.user.id)

    with pytest.raises(NotFoundError):
        await PipelineRunService.reproduce_run(session, foreign.id, admin_user.organization_id, admin_user.id)

    assert compute.submit_job.await_count == 0
