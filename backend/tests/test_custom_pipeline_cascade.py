"""Tests for the environment-build cascade that creates new pipeline versions (ADR-046)."""

from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.models.audit_log import AuditLog
from app.models.custom_pipeline_variable import CustomPipelineVariable
from app.models.custom_pipeline_version import CustomPipelineVersion
from app.models.environment import Environment
from app.models.environment_version import EnvironmentVersion
from app.schemas.custom_pipeline import (
    CustomPipelineCreateRequest,
    CustomPipelineVariableDefinition,
    CustomPipelineVersionCreateRequest,
)
from app.services.custom_pipeline_service import CustomPipelineService
from app.services.environment_build_service import EnvironmentBuildService
from app.services.event_bus import event_bus
from app.services.event_types import ENVIRONMENT_BUILD_COMPLETED


@pytest_asyncio.fixture(autouse=True)
def _clear_event_bus():
    event_bus.clear()
    yield
    event_bus.clear()


@pytest_asyncio.fixture
async def pipeline_env(session, admin_user):
    env = Environment(
        name="Cascade Pipeline Env",
        organization_id=admin_user.organization_id,
        created_by_user_id=admin_user.id,
        environment_type="pipeline",
    )
    session.add(env)
    await session.flush()

    v1 = EnvironmentVersion(
        environment_id=env.id,
        version_number=1,
        status="ready",
        definition_format="conda",
        definition_content="name: pipeline\nchannels: [conda-forge]\ndependencies: [python=3.11]\n",
        image_uri="projects/test/global/images/pipeline-v1",
        created_by_user_id=admin_user.id,
    )
    session.add(v1)
    await session.flush()
    await session.commit()
    return env, v1


async def _create_active_pipeline_version(
    session,
    admin_user,
    env_version_id: int,
    pipeline_name: str,
    variables: list[CustomPipelineVariableDefinition] | None = None,
):
    pipeline = await CustomPipelineService.create_pipeline(
        session,
        admin_user.organization_id,
        admin_user.id,
        CustomPipelineCreateRequest(name=pipeline_name),
    )
    version = await CustomPipelineService.create_version(
        session,
        admin_user.organization_id,
        admin_user.id,
        pipeline.id,
        CustomPipelineVersionCreateRequest(
            code_source_type="inline",
            code_content="print('hi')",
            entrypoint_command="python script.py",
            environment_version_id=env_version_id,
            cpu_request="2",
            memory_request="8Gi",
            log_file_path="/outputs/run.log",
            variables=variables or [],
        ),
    )
    await session.commit()
    return pipeline, version


@pytest.mark.asyncio
async def test_cascade_creates_new_pipeline_version(session, admin_user, pipeline_env):
    env, v1 = pipeline_env
    pipeline, source_version = await _create_active_pipeline_version(session, admin_user, v1.id, "Cascade Pipeline A")

    v2 = EnvironmentVersion(
        environment_id=env.id,
        version_number=2,
        status="ready",
        definition_format="conda",
        definition_content="name: pipeline\nchannels: [conda-forge]\ndependencies: [python=3.12]\n",
        image_uri="projects/test/global/images/pipeline-v2",
        created_by_user_id=admin_user.id,
    )
    session.add(v2)
    await session.flush()
    await session.commit()

    created = await CustomPipelineService.cascade_pipeline_versions(
        session, environment_id=env.id, environment_version_id=v2.id
    )
    await session.commit()

    assert len(created) == 1
    cascade_version = created[0]
    assert cascade_version.custom_pipeline_id == pipeline.id
    assert cascade_version.version_number == source_version.version_number + 1
    assert cascade_version.environment_version_id == v2.id
    assert cascade_version.version_trigger == "environment_cascade"
    assert cascade_version.status == "active"
    assert cascade_version.code_source_type == source_version.code_source_type
    assert cascade_version.code_content == source_version.code_content
    assert cascade_version.entrypoint_command == source_version.entrypoint_command
    assert cascade_version.cpu_request == source_version.cpu_request
    assert cascade_version.memory_request == source_version.memory_request
    assert cascade_version.log_file_path == source_version.log_file_path
    assert cascade_version.created_by_user_id == admin_user.id

    audit = (
        await session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "custom_pipeline_version",
                AuditLog.entity_id == cascade_version.id,
                AuditLog.action == "cascade_create",
            )
        )
    ).scalar_one()
    assert audit.user_id == admin_user.id


@pytest.mark.asyncio
async def test_cascade_copies_variables(session, admin_user, pipeline_env):
    env, v1 = pipeline_env
    variables = [
        CustomPipelineVariableDefinition(
            variable_name="threads",
            default_value="4",
            variable_type="number",
            is_required=True,
        ),
        CustomPipelineVariableDefinition(
            variable_name="mode",
            default_value="fast",
            variable_type="string",
            is_required=False,
        ),
    ]
    _, source_version = await _create_active_pipeline_version(
        session, admin_user, v1.id, "Vars Pipeline", variables=variables
    )

    v2 = EnvironmentVersion(
        environment_id=env.id,
        version_number=2,
        status="ready",
        definition_format="conda",
        definition_content="name: pipeline\nchannels: [conda-forge]\ndependencies: [python=3.12]\n",
        image_uri="projects/test/global/images/pipeline-v2",
        created_by_user_id=admin_user.id,
    )
    session.add(v2)
    await session.flush()
    await session.commit()

    created = await CustomPipelineService.cascade_pipeline_versions(
        session, environment_id=env.id, environment_version_id=v2.id
    )
    await session.commit()
    assert len(created) == 1
    cascade_version = created[0]

    cascade_vars = (
        (
            await session.execute(
                select(CustomPipelineVariable)
                .where(CustomPipelineVariable.custom_pipeline_version_id == cascade_version.id)
                .order_by(CustomPipelineVariable.variable_name)
            )
        )
        .scalars()
        .all()
    )
    assert len(cascade_vars) == 2

    threads = next(v for v in cascade_vars if v.variable_name == "threads")
    assert threads.default_value == "4"
    assert threads.variable_type == "number"
    assert threads.is_required is True

    mode = next(v for v in cascade_vars if v.variable_name == "mode")
    assert mode.default_value == "fast"
    assert mode.variable_type == "string"
    assert mode.is_required is False

    # Source variables remain untouched
    source_vars = (
        (
            await session.execute(
                select(CustomPipelineVariable).where(
                    CustomPipelineVariable.custom_pipeline_version_id == source_version.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(source_vars) == 2


@pytest.mark.asyncio
async def test_cascade_skips_already_up_to_date(session, admin_user, pipeline_env):
    env, v1 = pipeline_env
    pipeline, source_version = await _create_active_pipeline_version(session, admin_user, v1.id, "Up To Date Pipeline")

    # Latest pipeline version already points at v1; cascade event for v1 is a no-op
    created = await CustomPipelineService.cascade_pipeline_versions(
        session, environment_id=env.id, environment_version_id=v1.id
    )
    await session.commit()
    assert created == []

    versions = (
        (
            await session.execute(
                select(CustomPipelineVersion).where(CustomPipelineVersion.custom_pipeline_id == pipeline.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(versions) == 1
    assert versions[0].id == source_version.id


@pytest.mark.asyncio
async def test_cascade_multiple_pipelines(session, admin_user, pipeline_env):
    env, v1 = pipeline_env
    pipeline_a, _ = await _create_active_pipeline_version(session, admin_user, v1.id, "Pipeline A")
    pipeline_b, _ = await _create_active_pipeline_version(session, admin_user, v1.id, "Pipeline B")

    v2 = EnvironmentVersion(
        environment_id=env.id,
        version_number=2,
        status="ready",
        definition_format="conda",
        definition_content="name: pipeline\nchannels: [conda-forge]\ndependencies: [python=3.12]\n",
        image_uri="projects/test/global/images/pipeline-v2",
        created_by_user_id=admin_user.id,
    )
    session.add(v2)
    await session.flush()
    await session.commit()

    created = await CustomPipelineService.cascade_pipeline_versions(
        session, environment_id=env.id, environment_version_id=v2.id
    )
    await session.commit()
    assert len(created) == 2
    pipeline_ids = sorted(v.custom_pipeline_id for v in created)
    assert pipeline_ids == sorted([pipeline_a.id, pipeline_b.id])
    assert all(v.environment_version_id == v2.id for v in created)


@pytest.mark.asyncio
async def test_cascade_skips_pipelines_using_other_environments(session, admin_user, pipeline_env):
    env, v1 = pipeline_env
    pipeline_a, _ = await _create_active_pipeline_version(session, admin_user, v1.id, "Pipeline A")

    other_env = Environment(
        name="Other Pipeline Env",
        organization_id=admin_user.organization_id,
        created_by_user_id=admin_user.id,
        environment_type="pipeline",
    )
    session.add(other_env)
    await session.flush()
    other_v1 = EnvironmentVersion(
        environment_id=other_env.id,
        version_number=1,
        status="ready",
        definition_format="conda",
        definition_content="name: other\nchannels: [conda-forge]\ndependencies: [python=3.11]\n",
        image_uri="projects/test/global/images/other-v1",
        created_by_user_id=admin_user.id,
    )
    session.add(other_v1)
    await session.flush()
    await session.commit()

    pipeline_b, _ = await _create_active_pipeline_version(session, admin_user, other_v1.id, "Pipeline B")

    v2 = EnvironmentVersion(
        environment_id=env.id,
        version_number=2,
        status="ready",
        definition_format="conda",
        definition_content="name: pipeline\nchannels: [conda-forge]\ndependencies: [python=3.12]\n",
        image_uri="projects/test/global/images/pipeline-v2",
        created_by_user_id=admin_user.id,
    )
    session.add(v2)
    await session.flush()
    await session.commit()

    created = await CustomPipelineService.cascade_pipeline_versions(
        session, environment_id=env.id, environment_version_id=v2.id
    )
    await session.commit()
    assert len(created) == 1
    assert created[0].custom_pipeline_id == pipeline_a.id


@pytest.mark.asyncio
async def test_cascade_ignores_deprecated_versions(session, admin_user, pipeline_env):
    env, v1 = pipeline_env
    pipeline, source_version = await _create_active_pipeline_version(session, admin_user, v1.id, "Deprecated Pipeline")

    await CustomPipelineService.deprecate_version(
        session, admin_user.organization_id, admin_user.id, pipeline.id, source_version.id
    )
    await session.commit()

    v2 = EnvironmentVersion(
        environment_id=env.id,
        version_number=2,
        status="ready",
        definition_format="conda",
        definition_content="name: pipeline\nchannels: [conda-forge]\ndependencies: [python=3.12]\n",
        image_uri="projects/test/global/images/pipeline-v2",
        created_by_user_id=admin_user.id,
    )
    session.add(v2)
    await session.flush()
    await session.commit()

    created = await CustomPipelineService.cascade_pipeline_versions(
        session, environment_id=env.id, environment_version_id=v2.id
    )
    await session.commit()
    assert created == []


@pytest.mark.asyncio
async def test_handle_environment_build_completed_filters_non_pipeline():
    """If environment_type is not 'pipeline', the handler must short-circuit."""
    with patch("app.database.async_session_factory") as factory_mock:
        await CustomPipelineService.handle_environment_build_completed(
            {
                "environment_id": 1,
                "environment_version_id": 1,
                "environment_type": "notebook",
                "organization_id": 1,
            }
        )
        factory_mock.assert_not_called()

    with patch("app.database.async_session_factory") as factory_mock:
        await CustomPipelineService.handle_environment_build_completed(
            {
                "environment_id": 1,
                "environment_version_id": 1,
                "environment_type": "work_node",
                "organization_id": 1,
            }
        )
        factory_mock.assert_not_called()


@pytest.mark.asyncio
async def test_event_emitted_when_build_transitions_to_ready(session, admin_user):
    """poll_in_progress_builds emits ENVIRONMENT_BUILD_COMPLETED when a build succeeds."""
    env = Environment(
        name="Event Emission Env",
        organization_id=admin_user.organization_id,
        created_by_user_id=admin_user.id,
        environment_type="pipeline",
    )
    session.add(env)
    await session.flush()

    version = EnvironmentVersion(
        environment_id=env.id,
        version_number=1,
        status="building",
        build_id="build-test-123",
        definition_format="conda",
        definition_content="name: pipeline\nchannels: [conda-forge]\ndependencies: [python=3.11]\n",
        created_by_user_id=admin_user.id,
    )
    session.add(version)
    await session.flush()

    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES ('gcp_project_id', 'test-project') "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    )
    await session.commit()

    received: list[dict] = []

    async def listener(payload):
        received.append(payload)

    event_bus.subscribe(ENVIRONMENT_BUILD_COMPLETED, listener)

    with patch(
        "app.services.notebook_image_service.check_build_status",
        return_value="SUCCESS",
    ):
        changed = await EnvironmentBuildService.poll_in_progress_builds(session)
        await session.commit()

    assert changed == 1
    assert len(received) == 1
    payload = received[0]
    assert payload["environment_id"] == env.id
    assert payload["environment_version_id"] == version.id
    assert payload["environment_type"] == "pipeline"
    assert payload["organization_id"] == admin_user.organization_id


@pytest.mark.asyncio
async def test_event_not_emitted_on_build_failure(session, admin_user):
    """Failed builds must not emit ENVIRONMENT_BUILD_COMPLETED."""
    env = Environment(
        name="Failed Build Env",
        organization_id=admin_user.organization_id,
        created_by_user_id=admin_user.id,
        environment_type="pipeline",
    )
    session.add(env)
    await session.flush()

    version = EnvironmentVersion(
        environment_id=env.id,
        version_number=1,
        status="building",
        build_id="build-fail-456",
        definition_format="conda",
        definition_content="name: pipeline\nchannels: [conda-forge]\ndependencies: [python=3.11]\n",
        created_by_user_id=admin_user.id,
    )
    session.add(version)
    await session.flush()

    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES ('gcp_project_id', 'test-project') "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    )
    await session.commit()

    received: list[dict] = []

    async def listener(payload):
        received.append(payload)

    event_bus.subscribe(ENVIRONMENT_BUILD_COMPLETED, listener)

    with patch(
        "app.services.notebook_image_service.check_build_status",
        return_value="FAILURE",
    ):
        await EnvironmentBuildService.poll_in_progress_builds(session)
        await session.commit()

    assert received == []
