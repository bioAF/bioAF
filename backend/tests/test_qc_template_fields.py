"""Tests for the qc_template / qc_config_json columns on pipelines and dashboards.

These columns are *additive*. They store the per-pipeline template name and the
resolved render config so old runs always render the way they were generated.
"""

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def org(session):
    from app.models.organization import Organization

    o = Organization(name="QCConfig Org", setup_complete=True)
    session.add(o)
    await session.flush()
    await session.commit()
    return o


@pytest.mark.asyncio
async def test_pipeline_catalog_entry_has_qc_template_field(session, org):
    from app.models.pipeline_catalog_entry import PipelineCatalogEntry

    entry = PipelineCatalogEntry(
        organization_id=org.id,
        pipeline_key="nf-core/scrnaseq",
        name="scRNA-seq",
        source_type="builtin",
        qc_template="scrnaseq",
        qc_config_json={"template": "scrnaseq", "sections": []},
    )
    session.add(entry)
    await session.flush()

    assert entry.qc_template == "scrnaseq"
    assert entry.qc_config_json == {"template": "scrnaseq", "sections": []}


@pytest.mark.asyncio
async def test_pipeline_catalog_entry_qc_template_optional(session, org):
    """The column is nullable -- not every catalog entry sets a template."""
    from app.models.pipeline_catalog_entry import PipelineCatalogEntry

    entry = PipelineCatalogEntry(
        organization_id=org.id,
        pipeline_key="nf-core/test",
        name="Test",
        source_type="builtin",
    )
    session.add(entry)
    await session.flush()
    assert entry.qc_template is None
    assert entry.qc_config_json is None


@pytest.mark.asyncio
async def test_qc_dashboard_has_qc_config_json_field(session, org):
    from app.models.experiment import Experiment
    from app.models.pipeline_run import PipelineRun
    from app.models.qc_dashboard import QCDashboard
    from app.models.user import User
    from app.services.auth_service import AuthService
    from app.services.bootstrap_roles import seed_builtin_roles

    role_map = await seed_builtin_roles(session, org.id)
    user = User(
        email="qc@test.com",
        password_hash=AuthService.hash_password("x"),
        role_id=role_map["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.flush()

    exp = Experiment(organization_id=org.id, name="E", owner_user_id=user.id, status="processing")
    session.add(exp)
    await session.flush()

    run = PipelineRun(
        organization_id=org.id,
        experiment_id=exp.id,
        submitted_by_user_id=user.id,
        pipeline_name="nf-core/scrnaseq",
        status="completed",
        work_dir="/tmp/x",
    )
    session.add(run)
    await session.flush()

    config_snapshot = {"template": "scrnaseq", "sections": [{"id": "hero"}]}
    d = QCDashboard(
        organization_id=org.id,
        pipeline_run_id=run.id,
        experiment_id=exp.id,
        metrics_json={"cell_count": 1000},
        plots_json=[],
        status="ready",
        qc_config_json=config_snapshot,
    )
    session.add(d)
    await session.flush()

    assert d.qc_config_json == config_snapshot


@pytest.mark.asyncio
async def test_custom_pipeline_version_has_qc_template_fields(session, org):
    """Custom pipeline versions store the QC config alongside the version's
    code so editing config and code produce a new pipeline version together."""
    from app.models.custom_pipeline import CustomPipeline
    from app.models.custom_pipeline_version import CustomPipelineVersion
    from app.models.environment_version import EnvironmentVersion
    from app.models.environment import Environment
    from app.models.user import User
    from app.services.auth_service import AuthService
    from app.services.bootstrap_roles import seed_builtin_roles

    role_map = await seed_builtin_roles(session, org.id)
    user = User(
        email="cpv@test.com",
        password_hash=AuthService.hash_password("x"),
        role_id=role_map["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.flush()

    env = Environment(
        organization_id=org.id,
        name="default",
        created_by_user_id=user.id,
    )
    session.add(env)
    await session.flush()
    env_v = EnvironmentVersion(
        environment_id=env.id,
        version_number=1,
        definition_format="dockerfile",
        definition_content="FROM python:3.12",
        created_by_user_id=user.id,
    )
    session.add(env_v)
    await session.flush()

    cp = CustomPipeline(
        organization_id=org.id,
        name="My QC Pipeline",
        pipeline_key="my-qc-pipeline",
        created_by_user_id=user.id,
    )
    session.add(cp)
    await session.flush()

    custom_config = {
        "template": "custom",
        "sections": [{"id": "main", "metrics": ["foo"]}],
        "metrics": {"foo": {"label": "Foo", "format": "decimal"}},
    }
    v = CustomPipelineVersion(
        custom_pipeline_id=cp.id,
        version_number=1,
        code_source_type="inline",
        code_content="echo hi",
        entrypoint_command="bash run.sh",
        environment_version_id=env_v.id,
        created_by_user_id=user.id,
        qc_template="custom",
        qc_config_json=custom_config,
    )
    session.add(v)
    await session.flush()

    assert v.qc_template == "custom"
    assert v.qc_config_json == custom_config
