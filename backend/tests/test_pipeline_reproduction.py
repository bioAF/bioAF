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
from app.services.auth_service import AuthService
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


_UNSET = object()


async def _launchable_run(
    session,
    org_id: int,
    user_id: int,
    *,
    key: str = "nf-core/scrnaseq",
    name: str | None = None,
    stored_key: object = None,
    version: object = _UNSET,
    samples: int = 1,
    launch_fields: dict | None = None,
) -> PipelineRun:
    """A completed catalog run whose samples still have their reads: reproducible as it stands.

    Launched through the real launcher, so it records what a real launch records (its sheet, its
    stated design, its inputs). By default it is then made to look historical: no stored
    ``pipeline_key``, and a ``pipeline_name`` equal to the catalog key, which is what every nf-core
    run recorded before the key was stored looks like.
    """
    from app.schemas.pipeline_run import PipelineRunLaunchRequest

    await PipelineCatalogService.initialize_builtin_pipelines(session, org_id)
    experiment = Experiment(organization_id=org_id, name="Repro Exp", owner_user_id=user_id, status="fastq_uploaded")
    session.add(experiment)
    await session.flush()
    sample_ids = []
    for i in range(1, samples + 1):
        sample = Sample(experiment_id=experiment.id, external_id=f"S{i}", organism="Homo sapiens", tissue_type="PBMC")
        session.add(sample)
        await session.flush()
        sample_ids.append(sample.id)
        for read in ("R1", "R2"):
            f = File(
                organization_id=org_id,
                experiment_id=experiment.id,
                gcs_uri=f"gs://bucket/{org_id}/S{i}_{read}_001.fastq.gz",
                filename=f"S{i}_{read}_001.fastq.gz",
                file_type="fastq",
            )
            session.add(f)
            await session.flush()
            await session.execute(sample_files.insert().values(sample_id=sample.id, file_id=f.id))
    await session.commit()

    accepting = MagicMock()
    accepting.submit_job = AsyncMock(
        return_value=_job_submit_result_from_dict(
            {"job_id": "orig", "namespace": "bioaf-pipelines", "status": "queued"}
        )
    )
    with (
        patch("app.services.pipeline_run_service.get_compute_adapter", return_value=accepting),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        run = await PipelineRunService.launch_run(
            session,
            org_id,
            user_id,
            PipelineRunLaunchRequest(
                pipeline_key=key,
                experiment_id=experiment.id,
                sample_ids=sample_ids,
                parameters={"aligner": "cellranger"},
                **(launch_fields or {}),
            ),
        )
    run.pipeline_name = name if name is not None else key
    run.pipeline_key = stored_key
    if version is not _UNSET:
        run.pipeline_version = version
    run.status = "completed"
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
    audits_before = await _count(session, AuditLog, AuditLog.action.in_(["reproduce", "launch"]))

    response = await client.post(f"/api/pipeline-runs/{foreign.id}/reproduce", headers=_bearer(admin_token))

    assert response.status_code == 404
    session.expire_all()
    assert await _count(session, PipelineRun) == runs_before
    assert compute.submit_job.await_count == 0
    assert await _count(session, AuditLog, AuditLog.action.in_(["reproduce", "launch"])) == audits_before
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


# --- Catalog identity (owner decisions 2026-11-03) ---
#
# A run stores the catalog key it launched, and reproduction uses that key; the display name is
# never used to find a pipeline. A run recorded before the key was stored is reproducible only when
# its name is exactly a catalog key. The installed version must still be the one the run used.


async def _assert_refused_without_effects(response, session, compute, runs_before: int) -> None:
    assert response.status_code == 400, response.text
    session.expire_all()
    assert await _count(session, PipelineRun) == runs_before
    assert compute.submit_job.await_count == 0


@pytest.mark.asyncio
async def test_a_catalog_launch_records_the_catalog_key(client, session, admin_user, admin_token, compute):
    original = await _launchable_run(session, admin_user.organization_id, admin_user.id)
    body = {"pipeline_key": "nf-core/scrnaseq", "experiment_id": original.experiment_id}

    with patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock):
        response = await client.post("/api/pipeline-runs", json=body, headers=_bearer(admin_token))

    assert response.status_code == 200, response.text
    session.expire_all()
    assert (await session.get(PipelineRun, response.json()["id"])).pipeline_key == "nf-core/scrnaseq"


@pytest.mark.asyncio
async def test_a_run_whose_name_differs_from_its_key_reproduces_through_the_key(
    client, session, admin_user, admin_token, compute
):
    original = await _launchable_run(
        session,
        admin_user.organization_id,
        admin_user.id,
        key="bioaf-system-test",
        name="bioAF System Test",
        stored_key="bioaf-system-test",
    )

    with patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock):
        response = await client.post(f"/api/pipeline-runs/{original.id}/reproduce", headers=_bearer(admin_token))

    assert response.status_code == 200, response.text
    assert compute.submit_job.call_args.args[0].pipeline_key == "bioaf-system-test"
    session.expire_all()
    assert (await session.get(PipelineRun, response.json()["id"])).pipeline_key == "bioaf-system-test"


@pytest.mark.asyncio
async def test_a_historical_run_named_by_its_exact_key_still_reproduces(
    client, session, admin_user, admin_token, compute
):
    original = await _launchable_run(session, admin_user.organization_id, admin_user.id)
    assert original.pipeline_key is None

    with patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock):
        response = await client.post(f"/api/pipeline-runs/{original.id}/reproduce", headers=_bearer(admin_token))

    assert response.status_code == 200, response.text
    assert compute.submit_job.call_args.args[0].pipeline_key == "nf-core/scrnaseq"


@pytest.mark.asyncio
async def test_a_historical_run_named_by_display_name_is_refused(client, session, admin_user, admin_token, compute):
    original = await _launchable_run(
        session, admin_user.organization_id, admin_user.id, key="bioaf-system-test", name="bioAF System Test"
    )
    runs_before = await _count(session, PipelineRun)

    response = await client.post(f"/api/pipeline-runs/{original.id}/reproduce", headers=_bearer(admin_token))

    await _assert_refused_without_effects(response, session, compute, runs_before)
    assert "cannot be reproduced" in response.json()["detail"]


@pytest.mark.asyncio
async def test_a_stored_key_that_no_longer_resolves_does_not_fall_back_to_the_name(
    client, session, admin_user, admin_token, compute
):
    """The name here is a real catalog key; using it would launch a different pipeline."""
    original = await _launchable_run(
        session, admin_user.organization_id, admin_user.id, name="nf-core/scrnaseq", stored_key="nf-core/retired"
    )
    runs_before = await _count(session, PipelineRun)

    response = await client.post(f"/api/pipeline-runs/{original.id}/reproduce", headers=_bearer(admin_token))

    await _assert_refused_without_effects(response, session, compute, runs_before)


@pytest.mark.asyncio
async def test_a_run_from_a_different_pipeline_version_is_refused(client, session, admin_user, admin_token, compute):
    original = await _launchable_run(
        session, admin_user.organization_id, admin_user.id, stored_key="nf-core/scrnaseq", version="2.6.0"
    )
    runs_before = await _count(session, PipelineRun)

    response = await client.post(f"/api/pipeline-runs/{original.id}/reproduce", headers=_bearer(admin_token))

    await _assert_refused_without_effects(response, session, compute, runs_before)
    detail = response.json()["detail"]
    assert "2.6.0" in detail and "2.7.1" in detail


# --- Custom pipeline reproduction (owner decisions 2026-11-03) ---
#
# A run of a custom pipeline is reproduced through the custom launcher, on the version it ran (not
# the pipeline's current one), with its recorded variables, inputs and experiment/project. It needs
# the custom launch permission on top of the route's own, and anything that would make the relaunch
# differ from the original is refused before a run is created or a job submitted.


@pytest.fixture
def custom_compute():
    adapter = MagicMock()
    adapter.submit_job = AsyncMock(
        return_value=_job_submit_result_from_dict(
            {"job_id": "bioaf-custom-repro", "namespace": "bioaf-pipelines", "status": "queued"}
        )
    )
    with patch("app.services.custom_pipeline_service.get_compute_adapter", return_value=adapter):
        yield adapter


async def _custom_original(session, org_id: int, user_id: int, *, name: str = "Inhouse QC", inputs: int = 1):
    """A completed custom run, launched through the real launcher so its records are the real shape."""
    from app.models.environment import Environment
    from app.models.environment_version import EnvironmentVersion
    from app.models.project import Project
    from app.schemas.custom_pipeline import (
        CustomPipelineCreateRequest,
        CustomPipelineLaunchRequest,
        CustomPipelineVariableDefinition,
        CustomPipelineVariableValue,
        CustomPipelineVersionCreateRequest,
    )
    from app.services.custom_pipeline_service import CustomPipelineService

    env = Environment(name="Env", organization_id=org_id, created_by_user_id=user_id, environment_type="pipeline")
    session.add(env)
    await session.flush()
    env_version = EnvironmentVersion(
        environment_id=env.id,
        version_number=1,
        status="ready",
        definition_format="conda",
        definition_content="name: p\n",
        image_uri="registry.test/p:v1",
        created_by_user_id=user_id,
    )
    project = Project(organization_id=org_id, name="Custom Repro", owner_user_id=user_id)
    session.add_all([env_version, project])
    await session.flush()
    experiment = Experiment(
        organization_id=org_id, project_id=project.id, name="Custom Exp", owner_user_id=user_id, status="processing"
    )
    session.add(experiment)
    await session.flush()
    files = []
    for i in range(inputs):
        f = File(
            organization_id=org_id,
            experiment_id=experiment.id,
            gcs_uri=f"gs://bucket/custom_{i}.tsv",
            filename=f"custom_{i}.tsv",
            file_type="tsv",
        )
        session.add(f)
        files.append(f)
    await session.flush()

    pipeline = await CustomPipelineService.create_pipeline(
        session, org_id, user_id, CustomPipelineCreateRequest(name=name)
    )
    version = await CustomPipelineService.create_version(
        session,
        org_id,
        user_id,
        pipeline.id,
        CustomPipelineVersionCreateRequest(
            code_source_type="inline",
            code_content="echo qc",
            entrypoint_command="run-qc",
            environment_version_id=env_version.id,
            cpu_request="1",
            memory_request="1Gi",
            variables=[
                CustomPipelineVariableDefinition(variable_name="depth", variable_type="number", default_value="10"),
                CustomPipelineVariableDefinition(variable_name="label", variable_type="string"),
            ],
        ),
    )
    accepting = MagicMock()
    accepting.submit_job = AsyncMock(
        return_value=_job_submit_result_from_dict(
            {"job_id": "orig", "namespace": "bioaf-pipelines", "status": "queued"}
        )
    )
    with (
        patch("app.services.custom_pipeline_service.get_compute_adapter", return_value=accepting),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        run = await CustomPipelineService.launch_run(
            session,
            org_id,
            user_id,
            CustomPipelineLaunchRequest(
                version_id=version.id,
                experiment_id=experiment.id,
                project_id=project.id,
                input_file_ids=[f.id for f in files],
                variables=[
                    CustomPipelineVariableValue(variable_name="depth", variable_value="25"),
                    CustomPipelineVariableValue(variable_name="label", variable_value="batch 7"),
                ],
            ),
        )
    run.status = "completed"
    await session.commit()
    return SimpleNamespace(
        run=run,
        version=version,
        pipeline=pipeline,
        env_version=env_version,
        files=files,
        experiment=experiment,
        project=project,
    )


async def _reproduce(client, run_id: int, headers: dict):
    with patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock):
        return await client.post(f"/api/pipeline-runs/{run_id}/reproduce", headers=headers)


@pytest.mark.asyncio
async def test_custom_reproduction_relaunches_the_original_version_inputs_variables_and_context(
    client, session, admin_user, admin_token, custom_compute, compute
):
    from app.schemas.custom_pipeline import CustomPipelineVersionCreateRequest
    from app.services.custom_pipeline_service import CustomPipelineService

    org_id, user_id = admin_user.organization_id, admin_user.id
    original = await _custom_original(session, org_id, user_id)
    # A newer active version exists; reproduction must still run the one the original ran.
    await CustomPipelineService.create_version(
        session,
        org_id,
        user_id,
        original.pipeline.id,
        CustomPipelineVersionCreateRequest(
            code_source_type="inline",
            code_content="echo qc v2",
            entrypoint_command="run-qc --v2",
            environment_version_id=original.env_version.id,
            cpu_request="1",
            memory_request="1Gi",
        ),
    )
    await session.commit()

    original_id, version_id = original.run.id, original.version.id
    file_ids = [f.id for f in original.files]
    context = (original.experiment.id, original.project.id)

    response = await _reproduce(client, original_id, _bearer(admin_token))

    assert response.status_code == 200, response.text
    assert custom_compute.submit_job.await_count == 1
    assert compute.submit_job.await_count == 0
    session.expire_all()
    new_run = await session.get(PipelineRun, response.json()["id"])
    assert new_run.custom_pipeline_version_id == version_id
    assert new_run.parameters_json == {"depth": "25", "label": "batch 7"}
    assert new_run.input_files_json == file_ids
    assert (new_run.experiment_id, new_run.project_id) == context
    audit = (
        await session.execute(select(AuditLog).where(AuditLog.action == "reproduce", AuditLog.entity_id == new_run.id))
    ).scalar_one()
    assert audit.details_json["original_run_id"] == original_id


@pytest.mark.asyncio
async def test_a_custom_run_named_like_a_catalog_key_never_takes_the_catalog_path(
    client, session, admin_user, admin_token, custom_compute, compute
):
    org_id = admin_user.organization_id
    await PipelineCatalogService.initialize_builtin_pipelines(session, org_id)
    original = await _custom_original(session, org_id, admin_user.id, name="bioaf-system-test")

    response = await _reproduce(client, original.run.id, _bearer(admin_token))

    assert response.status_code == 200, response.text
    assert custom_compute.submit_job.await_count == 1
    assert compute.submit_job.await_count == 0


@pytest.mark.asyncio
async def test_a_run_with_no_inputs_recorded_as_none_reproduces(
    client, session, admin_user, admin_token, custom_compute
):
    original = await _custom_original(session, admin_user.organization_id, admin_user.id, inputs=0)
    assert original.run.input_files_json == []

    response = await _reproduce(client, original.run.id, _bearer(admin_token))

    assert response.status_code == 200, response.text
    assert custom_compute.submit_job.await_count == 1


async def _role_headers(session, org_id: int, grants: list[tuple[str, str]]) -> dict:
    from app.services import role_service

    role = await role_service.create_role(session, org_id, f"repro-{len(grants)}", permissions=grants)
    user = User(
        email=f"repro{len(grants)}@test.com",
        password_hash="x",
        role_id=role.id,
        organization_id=org_id,
        status="active",
    )
    session.add(user)
    await session.commit()
    role_service.invalidate_cache()
    return _bearer(AuthService.create_token(user.id, user.email, role.id, org_id, role_name=role.name))


@pytest.mark.asyncio
async def test_custom_reproduction_without_the_custom_launch_grant_is_forbidden(
    client, session, admin_user, custom_compute
):
    org_id = admin_user.organization_id
    original = await _custom_original(session, org_id, admin_user.id)
    headers = await _role_headers(session, org_id, [("pipelines", "launch"), ("pipelines", "view")])
    runs_before = await _count(session, PipelineRun)

    response = await _reproduce(client, original.run.id, headers)

    assert response.status_code == 403
    session.expire_all()
    assert await _count(session, PipelineRun) == runs_before
    assert custom_compute.submit_job.await_count == 0


@pytest.mark.asyncio
async def test_custom_reproduction_with_both_grants_is_allowed(client, session, admin_user, custom_compute):
    org_id = admin_user.organization_id
    original = await _custom_original(session, org_id, admin_user.id)
    headers = await _role_headers(session, org_id, [("pipelines", "launch"), ("custom_pipelines", "launch")])

    response = await _reproduce(client, original.run.id, headers)

    assert response.status_code == 200, response.text
    assert custom_compute.submit_job.await_count == 1


@pytest.mark.asyncio
async def test_an_api_key_without_launch_scopes_cannot_reproduce_a_custom_run(
    client, session, admin_user, custom_compute
):
    """Its role (admin) holds both grants; the key's scope envelope does not."""
    from tests.test_permissions_and_ownership import _api_key_headers

    original = await _custom_original(session, admin_user.organization_id, admin_user.id)
    headers = await _api_key_headers(session, admin_user, admin_user.role_id, ["experiments:view"])

    response = await _reproduce(client, original.run.id, headers)

    assert response.status_code == 403
    assert custom_compute.submit_job.await_count == 0


@pytest.mark.asyncio
async def test_the_reproduce_service_refuses_a_custom_run_unless_custom_launch_is_allowed(
    session, admin_user, custom_compute
):
    """Non-HTTP callers state the caller's custom launch permission; the default is no."""
    from app.exceptions import PermissionDeniedError

    org_id, user_id = admin_user.organization_id, admin_user.id
    original = await _custom_original(session, org_id, user_id)

    with pytest.raises(PermissionDeniedError):
        await PipelineRunService.reproduce_run(session, original.run.id, org_id, user_id)
    assert custom_compute.submit_job.await_count == 0


def _breakers():
    """Each makes the original no longer reproducible as recorded; all must refuse before any effect."""

    async def deprecate_version(session, o):
        o.version.status = "deprecated"

    async def unready_environment(session, o):
        o.env_version.status = "building"

    async def delete_input(session, o):
        from datetime import datetime, timezone

        o.files[0].deleted_at = datetime.now(timezone.utc)

    async def erase_input(session, o):
        o.files[0].storage_deleted = True

    async def json_only_inputs(session, o):
        from sqlalchemy import delete

        from app.models.pipeline_run_input_file import PipelineRunInputFile

        await session.execute(delete(PipelineRunInputFile).where(PipelineRunInputFile.pipeline_run_id == o.run.id))

    async def conflicting_inputs(session, o):
        o.run.input_files_json = [o.files[0].id, 999999]

    async def unknown_inputs(session, o):
        o.run.input_files_json = None

    async def malformed_inputs(session, o):
        o.run.input_files_json = {"ids": [o.files[0].id]}

    async def malformed_variables(session, o):
        o.run.parameters_json = ["depth", "25"]

    async def inaccessible_experiment(session, o):
        other = Organization(name="Elsewhere", setup_complete=True)
        session.add(other)
        await session.flush()
        o.experiment.organization_id = other.id

    return [
        deprecate_version,
        unready_environment,
        delete_input,
        erase_input,
        json_only_inputs,
        conflicting_inputs,
        unknown_inputs,
        malformed_inputs,
        malformed_variables,
        inaccessible_experiment,
    ]


@pytest.mark.parametrize("breaker", _breakers(), ids=lambda b: b.__name__)
@pytest.mark.asyncio
async def test_custom_reproduction_refuses_what_it_cannot_reproduce_as_recorded(
    client, session, admin_user, admin_token, custom_compute, breaker
):
    original = await _custom_original(session, admin_user.organization_id, admin_user.id)
    await breaker(session, original)
    await session.commit()
    runs_before = await _count(session, PipelineRun)

    response = await _reproduce(client, original.run.id, _bearer(admin_token))

    assert response.status_code in (400, 409), response.text
    session.expire_all()
    assert await _count(session, PipelineRun) == runs_before
    assert custom_compute.submit_job.await_count == 0


@pytest.mark.parametrize(
    "recorded,installed",
    [(None, None), ("", ""), (None, ""), ("", None), (None, "2.7.1"), ("2.7.1", None), ("2.7.1", "")],
    ids=["both-none", "both-empty", "none-vs-empty", "empty-vs-none", "unrecorded", "uninstalled", "installed-empty"],
)
@pytest.mark.asyncio
async def test_a_missing_version_on_either_side_is_refused(
    client, session, admin_user, admin_token, compute, recorded, installed
):
    """Two missing versions are not a match: neither says which code the run used."""
    from sqlalchemy import update

    from app.models.pipeline_catalog_entry import PipelineCatalogEntry

    org_id = admin_user.organization_id
    original = await _launchable_run(session, org_id, admin_user.id, stored_key="nf-core/scrnaseq", version=recorded)
    await session.execute(
        update(PipelineCatalogEntry)
        .where(PipelineCatalogEntry.organization_id == org_id, PipelineCatalogEntry.pipeline_key == "nf-core/scrnaseq")
        .values(version=installed)
    )
    await session.commit()
    runs_before = await _count(session, PipelineRun)

    response = await client.post(f"/api/pipeline-runs/{original.id}/reproduce", headers=_bearer(admin_token))

    await _assert_refused_without_effects(response, session, compute, runs_before)
