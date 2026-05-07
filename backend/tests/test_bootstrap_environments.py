"""Tests for the built-in environment seeder (to-resolve issue #1).

The seeder ships a system-managed `bioaf-base` work-node environment
that points at a pre-published image so first-launch is instant -- the
user does not have to wait for a per-install Packer build.
"""

import os
from contextlib import contextmanager

import pytest
from sqlalchemy import select

from app.models.environment import Environment
from app.models.environment_version import EnvironmentVersion


PUBLIC_IMAGE_URI = "projects/bioaf-public-images/global/images/bioaf-base-v1"


@contextmanager
def base_image_env(uri: str | None):
    """Context manager that sets / unsets BIOAF_BASE_WORK_NODE_IMAGE_URI."""
    prev = os.environ.get("BIOAF_BASE_WORK_NODE_IMAGE_URI")
    if uri is None:
        os.environ.pop("BIOAF_BASE_WORK_NODE_IMAGE_URI", None)
    else:
        os.environ["BIOAF_BASE_WORK_NODE_IMAGE_URI"] = uri
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("BIOAF_BASE_WORK_NODE_IMAGE_URI", None)
        else:
            os.environ["BIOAF_BASE_WORK_NODE_IMAGE_URI"] = prev


@pytest.mark.asyncio
async def test_seed_builtin_environments_creates_bioaf_base(session, admin_user):
    """When the public image URI is configured, the seeder registers
    `bioaf-base` as a ready work-node env pointing at that image."""
    from app.services.bootstrap_environments import seed_builtin_environments

    with base_image_env(PUBLIC_IMAGE_URI):
        await seed_builtin_environments(session)
        await session.commit()

    result = await session.execute(
        select(Environment).where(
            Environment.organization_id == admin_user.organization_id,
            Environment.name == "bioaf-base",
        )
    )
    env = result.scalar_one_or_none()
    assert env is not None, "bioaf-base environment should be seeded"
    assert env.environment_type == "work_node"
    assert env.visibility == "organization"

    versions_result = await session.execute(
        select(EnvironmentVersion).where(EnvironmentVersion.environment_id == env.id)
    )
    versions = list(versions_result.scalars().all())
    assert len(versions) == 1, "bioaf-base should have exactly one version"
    v = versions[0]
    assert v.status == "ready", "bioaf-base version must be ready (no user build needed)"
    assert v.image_uri == PUBLIC_IMAGE_URI
    assert v.definition_format == "conda"


@pytest.mark.asyncio
async def test_seed_builtin_environments_is_idempotent(session, admin_user):
    """Running the seeder twice does not create duplicate envs / versions."""
    from app.services.bootstrap_environments import seed_builtin_environments

    with base_image_env(PUBLIC_IMAGE_URI):
        await seed_builtin_environments(session)
        await seed_builtin_environments(session)
        await session.commit()

    envs_result = await session.execute(
        select(Environment).where(
            Environment.organization_id == admin_user.organization_id,
            Environment.name == "bioaf-base",
        )
    )
    envs = list(envs_result.scalars().all())
    assert len(envs) == 1

    versions_result = await session.execute(
        select(EnvironmentVersion).where(EnvironmentVersion.environment_id == envs[0].id)
    )
    versions = list(versions_result.scalars().all())
    assert len(versions) == 1


@pytest.mark.asyncio
async def test_seed_builtin_environments_skips_when_image_unset(session, admin_user):
    """Without a configured public image URI, the seeder is a no-op (it
    does not create a half-broken env that points at nothing)."""
    from app.services.bootstrap_environments import seed_builtin_environments

    with base_image_env(None):
        await seed_builtin_environments(session)
        await session.commit()

    result = await session.execute(
        select(Environment).where(
            Environment.organization_id == admin_user.organization_id,
            Environment.name == "bioaf-base",
        )
    )
    assert result.scalar_one_or_none() is None
