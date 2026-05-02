"""Tests for CustomPipelineService (custom pipelines CRUD + version management)."""

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.custom_pipeline import CustomPipeline
from app.models.custom_pipeline_variable import CustomPipelineVariable
from app.models.custom_pipeline_version import CustomPipelineVersion
from app.models.environment import Environment
from app.models.environment_version import EnvironmentVersion
from app.models.github_repo import GitHubRepo
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.schemas.custom_pipeline import (
    CustomPipelineCreateRequest,
    CustomPipelineUpdateRequest,
    CustomPipelineVariableDefinition,
    CustomPipelineVersionCreateRequest,
)
from app.services.custom_pipeline_service import CustomPipelineService


@pytest_asyncio.fixture
async def ready_env_version(session, admin_user):
    env = Environment(
        name="Pipeline Env",
        description="Ready pipeline environment",
        organization_id=admin_user.organization_id,
        created_by_user_id=admin_user.id,
        environment_type="pipeline",
    )
    session.add(env)
    await session.flush()

    version = EnvironmentVersion(
        environment_id=env.id,
        version_number=1,
        status="ready",
        definition_format="conda",
        definition_content="name: pipeline\nchannels: [conda-forge]\ndependencies: [python=3.11]\n",
        image_uri="projects/test/global/images/bioaf-pipeline-test-v1",
        created_by_user_id=admin_user.id,
    )
    session.add(version)
    await session.flush()
    await session.commit()
    return version


@pytest_asyncio.fixture
async def building_env_version(session, admin_user):
    env = Environment(
        name="Building Env",
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
        definition_format="conda",
        definition_content="name: pipeline\nchannels: [conda-forge]\ndependencies: [python=3.11]\n",
        created_by_user_id=admin_user.id,
    )
    session.add(version)
    await session.flush()
    await session.commit()
    return version


@pytest_asyncio.fixture
async def github_repo(session, admin_user):
    repo = GitHubRepo(
        user_id=admin_user.id,
        organization_id=admin_user.organization_id,
        git_ssh_url="git@github.com:example/repo.git",
        display_name="example/repo",
    )
    session.add(repo)
    await session.flush()
    await session.commit()
    return repo


# --- Pipeline CRUD ---


@pytest.mark.asyncio
async def test_create_pipeline_creates_catalog_entry(session, admin_user):
    pipeline = await CustomPipelineService.create_pipeline(
        session,
        admin_user.organization_id,
        admin_user.id,
        CustomPipelineCreateRequest(name="My Pipeline", description="Test pipeline"),
    )
    await session.commit()

    assert pipeline.id > 0
    assert pipeline.name == "My Pipeline"
    assert pipeline.description == "Test pipeline"
    assert pipeline.pipeline_key == "my-pipeline"
    assert pipeline.organization_id == admin_user.organization_id
    assert pipeline.created_by_user_id == admin_user.id

    catalog = (
        await session.execute(
            select(PipelineCatalogEntry).where(
                PipelineCatalogEntry.organization_id == admin_user.organization_id,
                PipelineCatalogEntry.custom_pipeline_id == pipeline.id,
            )
        )
    ).scalar_one()
    assert catalog.pipeline_key == "my-pipeline"
    assert catalog.name == "My Pipeline"
    assert catalog.description == "Test pipeline"
    assert catalog.source_type == "custom"
    assert catalog.is_builtin is False
    assert catalog.enabled is True


@pytest.mark.asyncio
async def test_create_pipeline_writes_audit_log(session, admin_user):
    pipeline = await CustomPipelineService.create_pipeline(
        session,
        admin_user.organization_id,
        admin_user.id,
        CustomPipelineCreateRequest(name="Audit Pipeline"),
    )
    await session.commit()

    audit = (
        await session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "custom_pipeline",
                AuditLog.entity_id == pipeline.id,
                AuditLog.action == "create",
            )
        )
    ).scalar_one()
    assert audit.user_id == admin_user.id


@pytest.mark.asyncio
async def test_create_pipeline_unique_key_with_collision(session, admin_user):
    p1 = await CustomPipelineService.create_pipeline(
        session,
        admin_user.organization_id,
        admin_user.id,
        CustomPipelineCreateRequest(name="My Pipeline"),
    )
    p2 = await CustomPipelineService.create_pipeline(
        session,
        admin_user.organization_id,
        admin_user.id,
        CustomPipelineCreateRequest(name="My Pipeline"),
    )
    p3 = await CustomPipelineService.create_pipeline(
        session,
        admin_user.organization_id,
        admin_user.id,
        CustomPipelineCreateRequest(name="My Pipeline"),
    )
    await session.commit()
    assert p1.pipeline_key == "my-pipeline"
    assert p2.pipeline_key == "my-pipeline-2"
    assert p3.pipeline_key == "my-pipeline-3"


@pytest.mark.asyncio
async def test_pipeline_key_strips_special_chars(session, admin_user):
    pipeline = await CustomPipelineService.create_pipeline(
        session,
        admin_user.organization_id,
        admin_user.id,
        CustomPipelineCreateRequest(name="My Cool Pipeline! v2.0 (Beta)"),
    )
    await session.commit()
    assert pipeline.pipeline_key == "my-cool-pipeline-v20-beta"


@pytest.mark.asyncio
async def test_list_pipelines(session, admin_user):
    await CustomPipelineService.create_pipeline(
        session,
        admin_user.organization_id,
        admin_user.id,
        CustomPipelineCreateRequest(name="Zeta"),
    )
    await CustomPipelineService.create_pipeline(
        session,
        admin_user.organization_id,
        admin_user.id,
        CustomPipelineCreateRequest(name="Alpha"),
    )
    await session.commit()

    pipelines = await CustomPipelineService.list_pipelines(session, admin_user.organization_id)
    assert len(pipelines) == 2
    assert [p.name for p in pipelines] == ["Alpha", "Zeta"]


@pytest.mark.asyncio
async def test_get_pipeline_loads_versions(session, admin_user, ready_env_version):
    pipeline = await CustomPipelineService.create_pipeline(
        session,
        admin_user.organization_id,
        admin_user.id,
        CustomPipelineCreateRequest(name="Detail Pipeline"),
    )
    await CustomPipelineService.create_version(
        session,
        admin_user.organization_id,
        admin_user.id,
        pipeline.id,
        CustomPipelineVersionCreateRequest(
            code_source_type="inline",
            code_content="print('hi')",
            entrypoint_command="python script.py",
            environment_version_id=ready_env_version.id,
        ),
    )
    await CustomPipelineService.create_version(
        session,
        admin_user.organization_id,
        admin_user.id,
        pipeline.id,
        CustomPipelineVersionCreateRequest(
            code_source_type="inline",
            code_content="print('v2')",
            entrypoint_command="python script.py",
            environment_version_id=ready_env_version.id,
        ),
    )
    await session.commit()

    detail = await CustomPipelineService.get_pipeline(session, admin_user.organization_id, pipeline.id)
    assert detail is not None
    assert len(detail.versions) == 2
    # Versions ordered by version_number desc
    assert detail.versions[0].version_number == 2
    assert detail.versions[1].version_number == 1


@pytest.mark.asyncio
async def test_get_pipeline_other_org(session, admin_user):
    pipeline = await CustomPipelineService.create_pipeline(
        session,
        admin_user.organization_id,
        admin_user.id,
        CustomPipelineCreateRequest(name="Org Scoped"),
    )
    await session.commit()

    result = await CustomPipelineService.get_pipeline(session, admin_user.organization_id + 999, pipeline.id)
    assert result is None


@pytest.mark.asyncio
async def test_update_pipeline(session, admin_user):
    pipeline = await CustomPipelineService.create_pipeline(
        session,
        admin_user.organization_id,
        admin_user.id,
        CustomPipelineCreateRequest(name="Original", description="Original desc"),
    )
    await session.commit()

    updated = await CustomPipelineService.update_pipeline(
        session,
        admin_user.organization_id,
        admin_user.id,
        pipeline.id,
        CustomPipelineUpdateRequest(name="Renamed", description="New desc"),
    )
    await session.commit()

    assert updated.name == "Renamed"
    assert updated.description == "New desc"

    catalog = (
        await session.execute(
            select(PipelineCatalogEntry).where(
                PipelineCatalogEntry.custom_pipeline_id == pipeline.id,
            )
        )
    ).scalar_one()
    assert catalog.name == "Renamed"
    assert catalog.description == "New desc"


@pytest.mark.asyncio
async def test_update_pipeline_not_found(session, admin_user):
    with pytest.raises(ValueError, match="not found"):
        await CustomPipelineService.update_pipeline(
            session,
            admin_user.organization_id,
            admin_user.id,
            99999,
            CustomPipelineUpdateRequest(name="Whatever"),
        )


@pytest.mark.asyncio
async def test_delete_pipeline_soft_disables_catalog(session, admin_user):
    pipeline = await CustomPipelineService.create_pipeline(
        session,
        admin_user.organization_id,
        admin_user.id,
        CustomPipelineCreateRequest(name="Soft Delete"),
    )
    await session.commit()

    await CustomPipelineService.delete_pipeline(session, admin_user.organization_id, admin_user.id, pipeline.id)
    await session.commit()

    # CustomPipeline still exists
    pipeline_row = (await session.execute(select(CustomPipeline).where(CustomPipeline.id == pipeline.id))).scalar_one()
    assert pipeline_row is not None

    # Catalog entry disabled
    catalog = (
        await session.execute(
            select(PipelineCatalogEntry).where(
                PipelineCatalogEntry.custom_pipeline_id == pipeline.id,
            )
        )
    ).scalar_one()
    assert catalog.enabled is False


@pytest.mark.asyncio
async def test_delete_pipeline_preserves_versions(session, admin_user, ready_env_version):
    pipeline = await CustomPipelineService.create_pipeline(
        session,
        admin_user.organization_id,
        admin_user.id,
        CustomPipelineCreateRequest(name="Versioned"),
    )
    version = await CustomPipelineService.create_version(
        session,
        admin_user.organization_id,
        admin_user.id,
        pipeline.id,
        CustomPipelineVersionCreateRequest(
            code_source_type="inline",
            code_content="print()",
            entrypoint_command="python script.py",
            environment_version_id=ready_env_version.id,
        ),
    )
    await session.commit()

    await CustomPipelineService.delete_pipeline(session, admin_user.organization_id, admin_user.id, pipeline.id)
    await session.commit()

    surviving = (
        await session.execute(select(CustomPipelineVersion).where(CustomPipelineVersion.id == version.id))
    ).scalar_one()
    assert surviving is not None


# --- Versions ---


@pytest.mark.asyncio
async def test_create_version_inline(session, admin_user, ready_env_version):
    pipeline = await CustomPipelineService.create_pipeline(
        session,
        admin_user.organization_id,
        admin_user.id,
        CustomPipelineCreateRequest(name="Inline Pipeline"),
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
            environment_version_id=ready_env_version.id,
            cpu_request="4",
            memory_request="16Gi",
        ),
    )
    await session.commit()

    assert version.version_number == 1
    assert version.code_source_type == "inline"
    assert version.code_content == "print('hi')"
    assert version.github_repo_id is None
    assert version.cpu_request == "4"
    assert version.memory_request == "16Gi"
    assert version.version_trigger == "user"
    assert version.status == "active"


@pytest.mark.asyncio
async def test_create_version_github_repo(session, admin_user, ready_env_version, github_repo):
    pipeline = await CustomPipelineService.create_pipeline(
        session,
        admin_user.organization_id,
        admin_user.id,
        CustomPipelineCreateRequest(name="Repo Pipeline"),
    )
    version = await CustomPipelineService.create_version(
        session,
        admin_user.organization_id,
        admin_user.id,
        pipeline.id,
        CustomPipelineVersionCreateRequest(
            code_source_type="github_repo",
            github_repo_id=github_repo.id,
            entrypoint_command="bash run.sh",
            environment_version_id=ready_env_version.id,
        ),
    )
    await session.commit()

    assert version.code_source_type == "github_repo"
    assert version.github_repo_id == github_repo.id
    assert version.code_content is None


@pytest.mark.asyncio
async def test_create_version_github_repo_missing_id(session, admin_user, ready_env_version):
    pipeline = await CustomPipelineService.create_pipeline(
        session,
        admin_user.organization_id,
        admin_user.id,
        CustomPipelineCreateRequest(name="Bad Pipeline"),
    )
    with pytest.raises(ValueError, match="github_repo_id is required"):
        await CustomPipelineService.create_version(
            session,
            admin_user.organization_id,
            admin_user.id,
            pipeline.id,
            CustomPipelineVersionCreateRequest(
                code_source_type="github_repo",
                entrypoint_command="bash run.sh",
                environment_version_id=ready_env_version.id,
            ),
        )


@pytest.mark.asyncio
async def test_create_version_code_blob_missing_content(session, admin_user, ready_env_version):
    pipeline = await CustomPipelineService.create_pipeline(
        session,
        admin_user.organization_id,
        admin_user.id,
        CustomPipelineCreateRequest(name="Bad Pipeline"),
    )
    with pytest.raises(ValueError, match="code_content is required"):
        await CustomPipelineService.create_version(
            session,
            admin_user.organization_id,
            admin_user.id,
            pipeline.id,
            CustomPipelineVersionCreateRequest(
                code_source_type="code_blob",
                entrypoint_command="python run.py",
                environment_version_id=ready_env_version.id,
            ),
        )


@pytest.mark.asyncio
async def test_create_version_environment_not_ready(session, admin_user, building_env_version):
    pipeline = await CustomPipelineService.create_pipeline(
        session,
        admin_user.organization_id,
        admin_user.id,
        CustomPipelineCreateRequest(name="Env Pipeline"),
    )
    with pytest.raises(ValueError, match="status 'ready'"):
        await CustomPipelineService.create_version(
            session,
            admin_user.organization_id,
            admin_user.id,
            pipeline.id,
            CustomPipelineVersionCreateRequest(
                code_source_type="inline",
                code_content="print()",
                entrypoint_command="python run.py",
                environment_version_id=building_env_version.id,
            ),
        )


@pytest.mark.asyncio
async def test_create_version_invalid_environment_id(session, admin_user):
    pipeline = await CustomPipelineService.create_pipeline(
        session,
        admin_user.organization_id,
        admin_user.id,
        CustomPipelineCreateRequest(name="Env Pipeline"),
    )
    with pytest.raises(ValueError, match="environment_version_id is not valid"):
        await CustomPipelineService.create_version(
            session,
            admin_user.organization_id,
            admin_user.id,
            pipeline.id,
            CustomPipelineVersionCreateRequest(
                code_source_type="inline",
                code_content="print()",
                entrypoint_command="python run.py",
                environment_version_id=999999,
            ),
        )


@pytest.mark.asyncio
async def test_create_version_invalid_log_path(session, admin_user, ready_env_version):
    pipeline = await CustomPipelineService.create_pipeline(
        session,
        admin_user.organization_id,
        admin_user.id,
        CustomPipelineCreateRequest(name="Log Pipeline"),
    )
    with pytest.raises(ValueError, match="/outputs/"):
        await CustomPipelineService.create_version(
            session,
            admin_user.organization_id,
            admin_user.id,
            pipeline.id,
            CustomPipelineVersionCreateRequest(
                code_source_type="inline",
                code_content="print()",
                entrypoint_command="python run.py",
                environment_version_id=ready_env_version.id,
                log_file_path="/tmp/log.txt",
            ),
        )


@pytest.mark.asyncio
async def test_version_number_auto_increments(session, admin_user, ready_env_version):
    pipeline = await CustomPipelineService.create_pipeline(
        session,
        admin_user.organization_id,
        admin_user.id,
        CustomPipelineCreateRequest(name="Increment"),
    )
    payload = CustomPipelineVersionCreateRequest(
        code_source_type="inline",
        code_content="print()",
        entrypoint_command="python run.py",
        environment_version_id=ready_env_version.id,
    )
    v1 = await CustomPipelineService.create_version(
        session, admin_user.organization_id, admin_user.id, pipeline.id, payload
    )
    v2 = await CustomPipelineService.create_version(
        session, admin_user.organization_id, admin_user.id, pipeline.id, payload
    )
    v3 = await CustomPipelineService.create_version(
        session, admin_user.organization_id, admin_user.id, pipeline.id, payload
    )
    await session.commit()
    assert (v1.version_number, v2.version_number, v3.version_number) == (1, 2, 3)


@pytest.mark.asyncio
async def test_version_creates_variables(session, admin_user, ready_env_version):
    pipeline = await CustomPipelineService.create_pipeline(
        session,
        admin_user.organization_id,
        admin_user.id,
        CustomPipelineCreateRequest(name="Vars"),
    )
    version = await CustomPipelineService.create_version(
        session,
        admin_user.organization_id,
        admin_user.id,
        pipeline.id,
        CustomPipelineVersionCreateRequest(
            code_source_type="inline",
            code_content="print()",
            entrypoint_command="python run.py",
            environment_version_id=ready_env_version.id,
            variables=[
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
            ],
        ),
    )
    await session.commit()

    rows = (
        (
            await session.execute(
                select(CustomPipelineVariable)
                .where(CustomPipelineVariable.custom_pipeline_version_id == version.id)
                .order_by(CustomPipelineVariable.variable_name)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    threads = next(r for r in rows if r.variable_name == "threads")
    assert threads.default_value == "4"
    assert threads.variable_type == "number"
    assert threads.is_required is True


@pytest.mark.asyncio
async def test_list_versions_orders_desc(session, admin_user, ready_env_version):
    pipeline = await CustomPipelineService.create_pipeline(
        session,
        admin_user.organization_id,
        admin_user.id,
        CustomPipelineCreateRequest(name="ListVer"),
    )
    payload = CustomPipelineVersionCreateRequest(
        code_source_type="inline",
        code_content="print()",
        entrypoint_command="python run.py",
        environment_version_id=ready_env_version.id,
    )
    await CustomPipelineService.create_version(session, admin_user.organization_id, admin_user.id, pipeline.id, payload)
    await CustomPipelineService.create_version(session, admin_user.organization_id, admin_user.id, pipeline.id, payload)
    await session.commit()

    versions = await CustomPipelineService.list_versions(session, admin_user.organization_id, pipeline.id)
    assert [v.version_number for v in versions] == [2, 1]


@pytest.mark.asyncio
async def test_deprecate_version(session, admin_user, ready_env_version):
    pipeline = await CustomPipelineService.create_pipeline(
        session,
        admin_user.organization_id,
        admin_user.id,
        CustomPipelineCreateRequest(name="Deprecator"),
    )
    version = await CustomPipelineService.create_version(
        session,
        admin_user.organization_id,
        admin_user.id,
        pipeline.id,
        CustomPipelineVersionCreateRequest(
            code_source_type="inline",
            code_content="print()",
            entrypoint_command="python run.py",
            environment_version_id=ready_env_version.id,
        ),
    )
    await session.commit()

    deprecated = await CustomPipelineService.deprecate_version(
        session, admin_user.organization_id, admin_user.id, pipeline.id, version.id
    )
    await session.commit()

    assert deprecated.status == "deprecated"

    audit = (
        await session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "custom_pipeline_version",
                AuditLog.entity_id == version.id,
                AuditLog.action == "deprecate",
            )
        )
    ).scalar_one()
    assert audit.user_id == admin_user.id
