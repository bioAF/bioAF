"""Tests for the environment-build cascade that creates new pipeline versions (ADR-046)."""

import copy
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from app.models.audit_log import AuditLog
from app.models.custom_pipeline_variable import CustomPipelineVariable
from app.models.custom_pipeline_version import CustomPipelineVersion
from app.models.environment import Environment
from app.models.environment_version import EnvironmentVersion
from app.models.pipeline_run import PipelineRun
from app.schemas.custom_pipeline import (
    CustomPipelineCreateRequest,
    CustomPipelineVariableDefinition,
    CustomPipelineVersionCreateRequest,
)
from app.services.custom_pipeline_service import CustomPipelineService
from app.services.environment_build_service import EnvironmentBuildService
from app.services.event_bus import event_bus
from app.services.event_types import ENVIRONMENT_BUILD_COMPLETED
from app.services.post_commit_events import wait_for_deferred_events
from app.services.qc.resolver import resolve_template_for_run


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
    qc_template: str | None = None,
    qc_config_json: dict | None = None,
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
            cpu_request="1",
            memory_request="4Gi",
            log_file_path="/outputs/run.log",
            variables=variables or [],
            qc_template=qc_template,
            qc_config_json=qc_config_json,
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

    for _k, _v in (("gcp_project_id", "test-project"), ("gcp_region", "us-central1")):
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=_k, v=_v)
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
    await wait_for_deferred_events()  # published after the commit

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

    for _k, _v in (("gcp_project_id", "test-project"), ("gcp_region", "us-central1")):
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=_k, v=_v)
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


# --- QC definition and reference scope survive a cascade ---


async def _add_env_version(session, admin_user, env, version_number: int) -> EnvironmentVersion:
    ev = EnvironmentVersion(
        environment_id=env.id,
        version_number=version_number,
        status="ready",
        definition_format="conda",
        definition_content=f"name: pipeline\ndependencies: [python=3.{10 + version_number}]\n",
        image_uri=f"projects/test/global/images/pipeline-v{version_number}",
        created_by_user_id=admin_user.id,
    )
    session.add(ev)
    await session.flush()
    await session.commit()
    return ev


async def _reload_version(session, version_id: int) -> CustomPipelineVersion:
    """Read a version back from the database. Callers capture ids first: this expires every loaded object."""
    session.expire_all()
    return (
        await session.execute(
            select(CustomPipelineVersion)
            .where(CustomPipelineVersion.id == version_id)
            .options(selectinload(CustomPipelineVersion.variables))
        )
    ).scalar_one()


NESTED_QC_CONFIG = {
    "template": "bulk_rnaseq",
    "sections": [{"id": "alignment", "metrics": ["mapped_pct", "dup_rate"]}],
    "metrics": {"mapped_pct": {"label": "Mapped", "format": "percent", "thresholds": {"warn": 70, "fail": 50}}},
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("qc_template", "qc_config_json"),
    [
        ("bulk_rnaseq", NESTED_QC_CONFIG),
        ("custom", {}),
        ("scrnaseq", None),
        (None, None),
    ],
    ids=["nested", "empty-config", "null-config", "no-qc"],
)
async def test_cascade_preserves_qc_definition_exactly(session, admin_user, pipeline_env, qc_template, qc_config_json):
    env, v1 = pipeline_env
    _, source = await _create_active_pipeline_version(
        session,
        admin_user,
        v1.id,
        f"QC Pipeline {qc_template}-{qc_config_json is None}",
        qc_template=qc_template,
        qc_config_json=copy.deepcopy(qc_config_json),
    )
    source_id = source.id
    v2 = await _add_env_version(session, admin_user, env, 2)

    created = await CustomPipelineService.cascade_pipeline_versions(
        session, environment_id=env.id, environment_version_id=v2.id
    )
    await session.commit()
    assert len(created) == 1

    cascade = await _reload_version(session, created[0].id)
    assert cascade.qc_template == qc_template
    assert cascade.qc_config_json == qc_config_json
    if qc_config_json is None:
        assert cascade.qc_config_json is None

    reloaded_source = await _reload_version(session, source_id)
    assert reloaded_source.qc_template == qc_template
    assert reloaded_source.qc_config_json == qc_config_json


@pytest.mark.asyncio
async def test_cascade_qc_config_is_independent_of_the_source_in_memory(session, admin_user, pipeline_env):
    env, v1 = pipeline_env
    _, source = await _create_active_pipeline_version(
        session,
        admin_user,
        v1.id,
        "Independent QC",
        qc_template="bulk_rnaseq",
        qc_config_json=copy.deepcopy(NESTED_QC_CONFIG),
    )
    source_id = source.id
    v2 = await _add_env_version(session, admin_user, env, 2)
    v2_id = v2.id
    source = await _reload_version(session, source_id)

    created = await CustomPipelineService.cascade_pipeline_versions(
        session, environment_id=source.environment_version.environment_id, environment_version_id=v2_id
    )
    cascade = created[0]

    cascade_config = cascade.qc_config_json
    assert cascade_config is not None
    cascade_config["metrics"]["mapped_pct"]["thresholds"]["warn"] = 99
    cascade_config["sections"][0]["metrics"].append("injected")

    assert source.qc_config_json == NESTED_QC_CONFIG


@pytest.mark.asyncio
async def test_qc_definition_survives_consecutive_cascades(session, admin_user, pipeline_env):
    env, v1 = pipeline_env
    pipeline, _ = await _create_active_pipeline_version(
        session,
        admin_user,
        v1.id,
        "Twice Rebuilt",
        qc_template="bulk_rnaseq",
        qc_config_json=copy.deepcopy(NESTED_QC_CONFIG),
        variables=[
            CustomPipelineVariableDefinition(
                variable_name="genome", variable_type="reference", reference_category="genome"
            )
        ],
    )

    v2 = await _add_env_version(session, admin_user, env, 2)
    first = await CustomPipelineService.cascade_pipeline_versions(
        session, environment_id=env.id, environment_version_id=v2.id
    )
    await session.commit()
    v3 = await _add_env_version(session, admin_user, env, 3)
    second = await CustomPipelineService.cascade_pipeline_versions(
        session, environment_id=env.id, environment_version_id=v3.id
    )
    await session.commit()

    assert len(first) == 1 and len(second) == 1
    pipeline_id, v3_id = pipeline.id, v3.id
    latest = await _reload_version(session, second[0].id)
    assert latest.custom_pipeline_id == pipeline_id
    assert latest.version_number == 3
    assert latest.environment_version_id == v3_id
    assert latest.qc_template == "bulk_rnaseq"
    assert latest.qc_config_json == NESTED_QC_CONFIG
    assert [(v.variable_name, v.reference_category) for v in latest.variables] == [("genome", "genome")]


@pytest.mark.asyncio
async def test_cascade_preserves_reference_picker_scope(session, admin_user, pipeline_env):
    env, v1 = pipeline_env
    _, source = await _create_active_pipeline_version(
        session,
        admin_user,
        v1.id,
        "Reference Pipeline",
        variables=[
            CustomPipelineVariableDefinition(
                variable_name="annotation_gtf",
                variable_type="reference",
                reference_category="annotation",
                is_required=True,
            ),
            CustomPipelineVariableDefinition(
                variable_name="any_reference", variable_type="reference", reference_category="any"
            ),
            CustomPipelineVariableDefinition(variable_name="threads", default_value="4", variable_type="number"),
        ],
    )
    source_id = source.id
    v2 = await _add_env_version(session, admin_user, env, 2)

    created = await CustomPipelineService.cascade_pipeline_versions(
        session, environment_id=env.id, environment_version_id=v2.id
    )
    await session.commit()
    cascade_id = created[0].id

    expected = {
        "annotation_gtf": ("reference", "annotation", True, None),
        "any_reference": ("reference", "any", False, None),
        "threads": ("number", None, False, "4"),
    }
    cascade = await _reload_version(session, cascade_id)
    assert {
        v.variable_name: (v.variable_type, v.reference_category, v.is_required, v.default_value)
        for v in cascade.variables
    } == expected
    cascade_variable_ids = {v.id for v in cascade.variables}

    reloaded_source = await _reload_version(session, source_id)
    assert {
        v.variable_name: (v.variable_type, v.reference_category, v.is_required, v.default_value)
        for v in reloaded_source.variables
    } == expected
    assert {v.id for v in reloaded_source.variables}.isdisjoint(cascade_variable_ids)


@pytest.mark.asyncio
async def test_run_on_cascade_version_resolves_the_source_qc_configuration(session, admin_user, pipeline_env):
    env, v1 = pipeline_env
    _, source = await _create_active_pipeline_version(
        session,
        admin_user,
        v1.id,
        "Resolver Pipeline",
        qc_template="bulk_rnaseq",
        qc_config_json=copy.deepcopy(NESTED_QC_CONFIG),
    )
    v2 = await _add_env_version(session, admin_user, env, 2)
    created = await CustomPipelineService.cascade_pipeline_versions(
        session, environment_id=env.id, environment_version_id=v2.id
    )
    await session.commit()

    source_run = PipelineRun(organization_id=admin_user.organization_id, custom_pipeline_version_id=source.id)
    cascade_run = PipelineRun(organization_id=admin_user.organization_id, custom_pipeline_version_id=created[0].id)

    source_resolved = await resolve_template_for_run(session, source_run)
    cascade_resolved = await resolve_template_for_run(session, cascade_run)

    assert source_resolved[0] == "bulk_rnaseq"
    assert cascade_resolved == source_resolved


# --- Organization isolation and preserved cascade policy ---


async def _cascade_audits(session) -> list[AuditLog]:
    session.expire_all()
    return list(
        (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.entity_type == "custom_pipeline_version",
                    AuditLog.action == "cascade_create",
                )
            )
        )
        .scalars()
        .all()
    )


def _details(audit: AuditLog) -> dict:
    assert audit.details_json is not None
    return audit.details_json


async def _versions_of(session, pipeline_id: int) -> list[CustomPipelineVersion]:
    session.expire_all()
    return list(
        (
            await session.execute(
                select(CustomPipelineVersion)
                .where(CustomPipelineVersion.custom_pipeline_id == pipeline_id)
                .order_by(CustomPipelineVersion.version_number)
            )
        )
        .scalars()
        .all()
    )


async def _foreign_org_user(session, email: str = "cascade-foreign@other.com"):
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.auth_service import AuthService
    from app.services.bootstrap_roles import seed_builtin_roles

    org = Organization(name="Cascade Foreign Org", setup_complete=True)
    session.add(org)
    await session.flush()
    role_map = await seed_builtin_roles(session, org.id)
    user = User(
        email=email,
        password_hash=AuthService.hash_password("foreignpass"),
        role_id=role_map["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return user


@pytest.mark.asyncio
async def test_cascade_skips_a_legacy_link_from_another_organization(session, admin_user, pipeline_env, caplog):
    env, v1 = pipeline_env
    own_pipeline, own_source = await _create_active_pipeline_version(session, admin_user, v1.id, "Own Pipeline")

    # Seeded directly: the repaired create_version path refuses this link.
    foreign_user = await _foreign_org_user(session)
    foreign_pipeline = await CustomPipelineService.create_pipeline(
        session, foreign_user.organization_id, foreign_user.id, CustomPipelineCreateRequest(name="Foreign Pipeline")
    )
    foreign_source = CustomPipelineVersion(
        custom_pipeline_id=foreign_pipeline.id,
        version_number=1,
        code_source_type="inline",
        code_content="print('foreign')",
        entrypoint_command="python foreign.py",
        environment_version_id=v1.id,
        created_by_user_id=foreign_user.id,
        qc_template="custom",
        qc_config_json={"metrics": {"secret_metric": {"label": "Secret"}}},
    )
    session.add(foreign_source)
    await session.flush()
    await session.commit()
    own_pipeline_id, own_source_id = own_pipeline.id, own_source.id
    foreign_pipeline_id, foreign_source_id = foreign_pipeline.id, foreign_source.id

    v2 = await _add_env_version(session, admin_user, env, 2)
    v2_id = v2.id
    with caplog.at_level("WARNING", logger="bioaf.custom_pipeline"):
        created = await CustomPipelineService.cascade_pipeline_versions(
            session, environment_id=env.id, environment_version_id=v2_id
        )
    await session.commit()

    assert [v.custom_pipeline_id for v in created] == [own_pipeline_id]
    assert [v.id for v in await _versions_of(session, foreign_pipeline_id)] == [foreign_source_id]
    audits = await _cascade_audits(session)
    assert len(audits) == 1
    assert _details(audits[0])["source_version_id"] == own_source_id
    assert _details(audits[0])["environment_version_id"] == v2_id

    skip_logs = [r.getMessage() for r in caplog.records if "organization_mismatch" in r.getMessage()]
    assert len(skip_logs) == 1
    assert str(foreign_source_id) in skip_logs[0]
    assert "secret_metric" not in caplog.text


@pytest.mark.asyncio
async def test_cascade_creates_nothing_when_the_target_belongs_to_another_environment(
    session, admin_user, pipeline_env, caplog
):
    env, v1 = pipeline_env
    pipeline, _ = await _create_active_pipeline_version(session, admin_user, v1.id, "Mismatched Target")
    pipeline_id = pipeline.id

    other_env = Environment(
        name="Unrelated Env",
        organization_id=admin_user.organization_id,
        created_by_user_id=admin_user.id,
        environment_type="pipeline",
    )
    session.add(other_env)
    await session.flush()
    other_v2 = await _add_env_version(session, admin_user, other_env, 2)

    with caplog.at_level("WARNING", logger="bioaf.custom_pipeline"):
        created = await CustomPipelineService.cascade_pipeline_versions(
            session, environment_id=env.id, environment_version_id=other_v2.id
        )
    await session.commit()

    assert created == []
    assert len(await _versions_of(session, pipeline_id)) == 1
    assert await _cascade_audits(session) == []
    assert "target_not_in_environment" in caplog.text


@pytest.mark.asyncio
async def test_cascade_creates_nothing_for_a_nonexistent_target(session, admin_user, pipeline_env):
    env, v1 = pipeline_env
    pipeline, _ = await _create_active_pipeline_version(session, admin_user, v1.id, "Missing Target")
    pipeline_id = pipeline.id

    created = await CustomPipelineService.cascade_pipeline_versions(
        session, environment_id=env.id, environment_version_id=999999
    )
    await session.commit()

    assert created == []
    assert len(await _versions_of(session, pipeline_id)) == 1
    assert await _cascade_audits(session) == []


@pytest.mark.asyncio
async def test_cascade_skips_a_pipeline_whose_latest_active_version_moved_to_another_environment(
    session, admin_user, pipeline_env
):
    env, v1 = pipeline_env
    pipeline, _ = await _create_active_pipeline_version(session, admin_user, v1.id, "Moved Pipeline")
    pipeline_id = pipeline.id

    other_env = Environment(
        name="Moved To Env",
        organization_id=admin_user.organization_id,
        created_by_user_id=admin_user.id,
        environment_type="pipeline",
    )
    session.add(other_env)
    await session.flush()
    other_v1 = await _add_env_version(session, admin_user, other_env, 1)
    await CustomPipelineService.create_version(
        session,
        admin_user.organization_id,
        admin_user.id,
        pipeline_id,
        CustomPipelineVersionCreateRequest(
            code_source_type="inline",
            code_content="print('moved')",
            entrypoint_command="python moved.py",
            environment_version_id=other_v1.id,
        ),
    )
    await session.commit()

    v2 = await _add_env_version(session, admin_user, env, 2)
    created = await CustomPipelineService.cascade_pipeline_versions(
        session, environment_id=env.id, environment_version_id=v2.id
    )
    await session.commit()

    assert created == []
    assert [v.version_number for v in await _versions_of(session, pipeline_id)] == [1, 2]


@pytest.mark.asyncio
async def test_repeated_build_notification_cascades_once(session, admin_user, pipeline_env):
    env, v1 = pipeline_env
    pipeline, source = await _create_active_pipeline_version(session, admin_user, v1.id, "Repeated Event")
    pipeline_id, source_id, v1_id = pipeline.id, source.id, v1.id
    v2 = await _add_env_version(session, admin_user, env, 2)
    env_id, v2_id = env.id, v2.id

    first = await CustomPipelineService.cascade_pipeline_versions(
        session, environment_id=env_id, environment_version_id=v2_id
    )
    await session.commit()
    second = await CustomPipelineService.cascade_pipeline_versions(
        session, environment_id=env_id, environment_version_id=v2_id
    )
    await session.commit()

    assert len(first) == 1
    assert second == []
    versions = await _versions_of(session, pipeline_id)
    assert [(v.version_number, v.environment_version_id) for v in versions] == [(1, v1_id), (2, v2_id)]
    audits = await _cascade_audits(session)
    assert [(_details(a)["source_version_id"], _details(a)["environment_version_id"]) for a in audits] == [
        (source_id, v2_id)
    ]


@pytest.mark.asyncio
async def test_cascade_attributes_the_version_to_the_environment_builder(session, admin_user, pipeline_env):
    from app.models.user import User

    env, v1 = pipeline_env
    pipeline, source = await _create_active_pipeline_version(session, admin_user, v1.id, "Attribution")
    builder = User(
        email="builder@test.com",
        password_hash="x",
        role_id=admin_user._test_role_map["comp_bio"],
        organization_id=admin_user.organization_id,
        status="active",
    )
    session.add(builder)
    await session.flush()
    v2 = EnvironmentVersion(
        environment_id=env.id,
        version_number=2,
        status="ready",
        definition_format="conda",
        definition_content="name: pipeline\n",
        created_by_user_id=builder.id,
    )
    session.add(v2)
    await session.flush()
    await session.commit()
    builder_id, source_id, source_number, v2_id = builder.id, source.id, source.version_number, v2.id
    pipeline_id = pipeline.id

    created = await CustomPipelineService.cascade_pipeline_versions(
        session, environment_id=env.id, environment_version_id=v2_id
    )
    await session.commit()
    cascade_id = created[0].id

    cascade = await _reload_version(session, cascade_id)
    assert cascade.created_by_user_id == builder_id
    assert cascade.version_number == source_number + 1
    (audit,) = await _cascade_audits(session)
    assert audit.user_id == builder_id
    assert audit.entity_id == cascade_id
    assert _details(audit)["source_version_id"] == source_id
    assert _details(audit)["environment_version_id"] == v2_id
    assert _details(audit)["custom_pipeline_id"] == pipeline_id
