"""Plan 03.5B: a system-provided notebook definition is identified by provenance, never by its name.

A seeded Default Notebook carries an explicit template identity and revision. A version made from a
different definition is user-managed (the environment keeps its ancestry); a rebuild keeps the
version's provenance. A legacy environment named "Default Notebook" without provenance is left as it is.
"""

import pytest
from sqlalchemy import select

from app.models.environment import Environment
from app.models.environment_version import EnvironmentVersion
from app.services.environment_service import (
    DEFAULT_NOTEBOOK_CONDA_YML,
    DEFAULT_NOTEBOOK_TEMPLATE,
    DEFAULT_NOTEBOOK_TEMPLATE_REVISION,
    EnvironmentService,
    ensure_default_notebook_environment,
)

CUSTOM = "name: mine\nchannels: [conda-forge]\ndependencies: [python=3.11, jupyterlab, r-base]\n"


async def _seeded(session):
    await ensure_default_notebook_environment(session)
    await session.commit()
    env = (await session.execute(select(Environment).where(Environment.environment_type == "notebook"))).scalar_one()
    version = (
        await session.execute(select(EnvironmentVersion).where(EnvironmentVersion.environment_id == env.id))
    ).scalar_one()
    return env, version


@pytest.mark.asyncio
async def test_the_seeded_default_notebook_carries_its_template_identity(session, admin_user):
    env, version = await _seeded(session)
    assert (env.template_key, env.template_revision) == (DEFAULT_NOTEBOOK_TEMPLATE, DEFAULT_NOTEBOOK_TEMPLATE_REVISION)
    assert version.template_revision == DEFAULT_NOTEBOOK_TEMPLATE_REVISION
    assert version.definition_content == DEFAULT_NOTEBOOK_CONDA_YML


@pytest.mark.asyncio
async def test_a_customized_version_is_user_managed_and_keeps_its_ancestry(session, admin_user):
    env, _ = await _seeded(session)
    custom = await EnvironmentService.create_version(
        session, admin_user.organization_id, admin_user.id, env.id, "conda", CUSTOM
    )
    unchanged = await EnvironmentService.create_version(
        session, admin_user.organization_id, admin_user.id, env.id, "conda", DEFAULT_NOTEBOOK_CONDA_YML
    )
    await session.commit()
    assert custom.template_revision is None
    assert unchanged.template_revision == DEFAULT_NOTEBOOK_TEMPLATE_REVISION
    await session.refresh(env)
    assert env.template_key == DEFAULT_NOTEBOOK_TEMPLATE


@pytest.mark.asyncio
async def test_a_rebuild_keeps_the_versions_provenance(session, admin_user):
    env, version = await _seeded(session)
    rebuild = await EnvironmentService.rebuild_version(
        session, admin_user.organization_id, admin_user.id, env.id, version.id
    )
    await session.commit()
    assert rebuild.template_revision == version.template_revision


@pytest.mark.asyncio
async def test_a_legacy_default_notebook_is_left_unchanged(session, admin_user):
    legacy = Environment(
        name="Default Notebook",
        organization_id=admin_user.organization_id,
        created_by_user_id=admin_user.id,
        environment_type="notebook",
    )
    session.add(legacy)
    await session.flush()
    session.add(
        EnvironmentVersion(
            environment_id=legacy.id,
            version_number=1,
            status="ready",
            definition_format="conda",
            definition_content=DEFAULT_NOTEBOOK_CONDA_YML,
            created_by_user_id=admin_user.id,
        )
    )
    await session.commit()
    await ensure_default_notebook_environment(session)
    await session.commit()
    envs = (
        (await session.execute(select(Environment).where(Environment.environment_type == "notebook"))).scalars().all()
    )
    assert [(e.id, e.template_key) for e in envs] == [(legacy.id, None)]
