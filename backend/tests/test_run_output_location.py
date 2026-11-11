"""A loaded run's one authorized output location, with stored values revalidated.

Database-backed: the run, its experiment/project and the platform config are real rows, and the
storage adapter is the real local one. A stored ``outdir`` is history, not authority: it predates
any check, so every consumer revalidates it against the run's own identity.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from app.adapters.storage.gcs import GcsStorageProvider
from app.models.experiment import Experiment
from app.models.pipeline_run import PipelineRun
from app.models.component import PlatformConfig
from app.models.project import Project
from app.services.run_output_location_service import OutputLocationRefused, RunOutputLocationService


@pytest_asyncio.fixture
async def experiment(session, admin_user):
    exp = Experiment(organization_id=admin_user.organization_id, name="E", owner_user_id=admin_user.id)
    session.add(exp)
    await session.flush()
    return exp


async def _run(session, admin_user, experiment, **kw):
    run = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=kw.pop("experiment_id", experiment.id),
        submitted_by_user_id=admin_user.id,
        pipeline_name="nf-core/rnaseq",
        status="completed",
        **kw,
    )
    session.add(run)
    await session.flush()
    return run


async def _config(session, **values):
    for key, value in values.items():
        session.add(PlatformConfig(key=key, value=value))
    await session.flush()


@pytest.fixture
def storage(monkeypatch):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "local")
    return GcsStorageProvider(org_slug="t")


@pytest.mark.asyncio
async def test_a_run_writes_under_its_own_experiment_in_the_results_bucket(session, admin_user, experiment, storage):
    await _config(session, results_bucket_name="res")
    run = await _run(session, admin_user, experiment)
    uri = await RunOutputLocationService.authorized_uri(session, run, storage)
    assert uri == f"gs://res/experiments/{experiment.id}/pipeline-runs/{run.id}"


@pytest.mark.asyncio
async def test_the_results_bucket_is_derived_from_the_raw_bucket(session, admin_user, experiment, storage):
    await _config(session, raw_bucket_name="bioaf-raw-acme")
    run = await _run(session, admin_user, experiment)
    uri = await RunOutputLocationService.authorized_uri(session, run, storage)
    assert uri == f"gs://bioaf-results-acme/experiments/{experiment.id}/pipeline-runs/{run.id}"


@pytest.mark.asyncio
async def test_with_no_bucket_configured_the_adapter_names_its_results_store(session, admin_user, experiment, storage):
    run = await _run(session, admin_user, experiment)
    uri = await RunOutputLocationService.authorized_uri(session, run, storage)
    assert uri == f"gs://bioaf-results-t/experiments/{experiment.id}/pipeline-runs/{run.id}"


@pytest.mark.asyncio
async def test_a_cloud_install_with_no_results_bucket_is_refused(session, admin_user, experiment, monkeypatch):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "k8s")
    storage = GcsStorageProvider(org_slug="t")
    storage._bucket_config = {}
    run = await _run(session, admin_user, experiment)
    with pytest.raises(OutputLocationRefused):
        await RunOutputLocationService.authorized_uri(session, run, storage)


@pytest.mark.asyncio
async def test_a_stored_outdir_equal_to_the_location_is_accepted(session, admin_user, experiment, storage):
    await _config(session, results_bucket_name="res")
    run = await _run(session, admin_user, experiment)
    run.parameters_json = {"outdir": f"gs://res/experiments/{experiment.id}/pipeline-runs/{run.id}/"}
    uri = await RunOutputLocationService.authorized_uri(session, run, storage)
    assert uri == f"gs://res/experiments/{experiment.id}/pipeline-runs/{run.id}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outdir",
    [
        "gs://other-bucket/",
        "gs://res/experiments/{exp}/pipeline-runs/{other}",
        "gs://res/experiments/{exp}/pipeline-runs/{run}/../{other}",
        "gs://res/experiments/{exp}/pipeline-runs/{run}/sub",
        "/etc",
        "file://results/experiments/{exp}/pipeline-runs/{run}",
    ],
)
async def test_any_other_stored_outdir_is_refused(session, admin_user, experiment, storage, outdir):
    await _config(session, results_bucket_name="res")
    run = await _run(session, admin_user, experiment)
    run.parameters_json = {"outdir": outdir.format(exp=experiment.id, run=run.id, other=run.id + 1)}
    with pytest.raises(OutputLocationRefused):
        await RunOutputLocationService.authorized_uri(session, run, storage)


@pytest.mark.asyncio
async def test_a_custom_pipeline_variable_named_outdir_is_not_a_location(session, admin_user, experiment, storage):
    from app.models.custom_pipeline import CustomPipeline
    from app.models.custom_pipeline_version import CustomPipelineVersion
    from app.models.environment import Environment
    from app.models.environment_version import EnvironmentVersion

    await _config(session, results_bucket_name="res")
    org_id, uid = admin_user.organization_id, admin_user.id
    env = Environment(name="e", organization_id=org_id, created_by_user_id=uid, environment_type="pipeline")
    session.add(env)
    await session.flush()
    env_version = EnvironmentVersion(
        environment_id=env.id,
        version_number=1,
        status="ready",
        definition_format="conda",
        definition_content="name: env\n",
        created_by_user_id=uid,
    )
    session.add(env_version)
    cp = CustomPipeline(organization_id=org_id, name="cp", pipeline_key=f"cp-{uid}", created_by_user_id=uid)
    session.add(cp)
    await session.flush()
    version = CustomPipelineVersion(
        custom_pipeline_id=cp.id,
        version_number=1,
        code_source_type="inline",
        entrypoint_command="run",
        environment_version_id=env_version.id,
        created_by_user_id=uid,
    )
    session.add(version)
    await session.flush()
    run = await _run(session, admin_user, experiment, custom_pipeline_version_id=version.id)
    run.parameters_json = {"outdir": "gs://other-bucket/"}
    uri = await RunOutputLocationService.authorized_uri(session, run, storage)
    assert uri == f"gs://res/experiments/{experiment.id}/pipeline-runs/{run.id}"


@pytest.mark.asyncio
async def test_a_project_scoped_run_lives_under_its_project(session, admin_user, experiment, storage):
    await _config(session, results_bucket_name="res")
    project = Project(organization_id=admin_user.organization_id, name="P", owner_user_id=admin_user.id)
    session.add(project)
    await session.flush()
    run = await _run(session, admin_user, experiment, experiment_id=None, project_id=project.id)
    uri = await RunOutputLocationService.authorized_uri(session, run, storage)
    assert uri == f"gs://res/projects/{project.id}/pipeline-runs/{run.id}"
