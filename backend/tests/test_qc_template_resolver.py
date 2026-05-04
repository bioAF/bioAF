"""Tests for resolve_template_for_run() in app.services.qc.resolver.

Given a pipeline run, the resolver returns:
- template_name: which QC template to use (string)
- render_config: the resolved render config dict (template default deep-merged
  with the pipeline's qc_config_json override, if any)

Resolution order (per spec):
1. If run.custom_pipeline_version_id is set, use that version's qc_template +
   qc_config_json override.
2. Otherwise look up a pipeline_catalog entry by (organization_id, pipeline_key
   == run.pipeline_name) and use its qc_template + qc_config_json override.
3. Fallback to "scrnaseq" if neither resolves (matches current product reality).
"""

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def org_user_exp(session):
    from app.models.experiment import Experiment
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.auth_service import AuthService
    from app.services.bootstrap_roles import seed_builtin_roles

    org = Organization(name="ResolverOrg", setup_complete=True)
    session.add(org)
    await session.flush()
    role_map = await seed_builtin_roles(session, org.id)
    user = User(
        email="resolver@test.com",
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
    await session.commit()
    return org, user, exp


@pytest.mark.asyncio
async def test_resolves_catalog_entry_template(session, org_user_exp):
    from app.models.pipeline_catalog_entry import PipelineCatalogEntry
    from app.models.pipeline_run import PipelineRun
    from app.services.qc.resolver import resolve_template_for_run

    org, user, exp = org_user_exp

    entry = PipelineCatalogEntry(
        organization_id=org.id,
        pipeline_key="nf-core/scrnaseq",
        name="scRNA-seq",
        source_type="builtin",
        qc_template="scrnaseq",
    )
    session.add(entry)
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

    template_name, cfg = await resolve_template_for_run(session, run)
    assert template_name == "scrnaseq"
    assert cfg["template"] == "scrnaseq"
    # Falls through to template default render config when no override
    assert any(s["id"] == "hero" for s in cfg["sections"])


@pytest.mark.asyncio
async def test_resolves_custom_pipeline_version_with_override(session, org_user_exp):
    from app.models.custom_pipeline import CustomPipeline
    from app.models.custom_pipeline_version import CustomPipelineVersion
    from app.models.environment import Environment
    from app.models.environment_version import EnvironmentVersion
    from app.models.pipeline_run import PipelineRun
    from app.services.qc.resolver import resolve_template_for_run

    org, user, exp = org_user_exp

    env = Environment(organization_id=org.id, name="default", created_by_user_id=user.id)
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

    override = {
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
        qc_config_json=override,
    )
    session.add(v)
    await session.flush()

    run = PipelineRun(
        organization_id=org.id,
        experiment_id=exp.id,
        submitted_by_user_id=user.id,
        pipeline_name="my-qc-pipeline",
        status="completed",
        work_dir="/tmp/x",
        custom_pipeline_version_id=v.id,
    )
    session.add(run)
    await session.flush()

    template_name, cfg = await resolve_template_for_run(session, run)
    assert template_name == "custom"
    assert cfg["sections"][0]["metrics"] == ["foo"]
    assert cfg["metrics"]["foo"]["label"] == "Foo"


@pytest.mark.asyncio
async def test_falls_back_to_scrnaseq_when_no_match(session, org_user_exp):
    from app.models.pipeline_run import PipelineRun
    from app.services.qc.resolver import resolve_template_for_run

    org, user, exp = org_user_exp

    run = PipelineRun(
        organization_id=org.id,
        experiment_id=exp.id,
        submitted_by_user_id=user.id,
        pipeline_name="nf-core/unknown-pipeline",
        status="completed",
        work_dir="/tmp/x",
    )
    session.add(run)
    await session.flush()

    template_name, cfg = await resolve_template_for_run(session, run)
    assert template_name == "scrnaseq"
    assert cfg["template"] == "scrnaseq"


@pytest.mark.asyncio
async def test_catalog_entry_override_deep_merged(session, org_user_exp):
    """When the catalog entry carries a qc_config_json override, it
    deep-merges over the template default."""
    from app.models.pipeline_catalog_entry import PipelineCatalogEntry
    from app.models.pipeline_run import PipelineRun
    from app.services.qc.resolver import resolve_template_for_run

    org, user, exp = org_user_exp

    override = {
        "metrics": {
            "cell_count": {"label": "Custom Cell Count Label"},
        }
    }
    entry = PipelineCatalogEntry(
        organization_id=org.id,
        pipeline_key="nf-core/scrnaseq",
        name="scRNA-seq",
        source_type="builtin",
        qc_template="scrnaseq",
        qc_config_json=override,
    )
    session.add(entry)
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

    _, cfg = await resolve_template_for_run(session, run)
    # Override label wins
    assert cfg["metrics"]["cell_count"]["label"] == "Custom Cell Count Label"
    # Other template metrics still present (deep merge, not replace)
    assert "saturation" in cfg["metrics"]
    # Sections still present from template default
    assert any(s["id"] == "hero" for s in cfg["sections"])
