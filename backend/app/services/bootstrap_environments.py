"""Seed built-in environments at startup.

Mirrors the pattern in `bootstrap_roles.seed_builtin_roles`: registers
system-managed environments that ship with every install so users
have something to pick on first launch.

Currently seeds:
- `bioaf-base` (work_node) -- points at a pre-published GCE image so
  first-launch is instant. The image is published out-of-band; this
  seeder only registers the metadata.
"""

import logging
import os

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.environment import Environment
from app.models.environment_version import EnvironmentVersion

logger = logging.getLogger("bioaf.bootstrap_environments")

BIOAF_BASE_NAME = "bioaf-base"
BIOAF_BASE_DESCRIPTION = (
    "Built-in base work-node environment. Python 3.11 with common "
    "scientific packages and Cloud SDK. Ships pre-built so first launch "
    "is instant -- no Packer build needed. Customize by creating a new "
    "environment on top."
)

# Conda environment.yml that the published image is built from. Stored
# as `definition_content` for transparency / auditability; the version
# is marked `ready` with `image_uri` populated, so no per-install build
# runs against this content.
BIOAF_BASE_CONDA_YML = """\
name: bioaf-base
channels:
  - conda-forge
  - bioconda
dependencies:
  - python=3.11
  - numpy
  - pandas
  - matplotlib
  - jupyter
  - ipykernel
  - pip
  - pip:
      - google-cloud-storage
"""


def _get_base_image_uri() -> str | None:
    """Return the pre-published bioaf-base image URI, or None if unset."""
    uri = os.environ.get("BIOAF_BASE_WORK_NODE_IMAGE_URI")
    return uri.strip() if uri and uri.strip() else None


async def seed_builtin_environments(session: AsyncSession) -> None:
    """Register `bioaf-base` for the org if a published image is configured.

    No-op if `BIOAF_BASE_WORK_NODE_IMAGE_URI` is not set: better to fall
    back to the existing `ensure_default_work_node_environment` draft
    flow than to register an env whose `image_uri` points at nothing.
    """
    image_uri = _get_base_image_uri()
    if not image_uri:
        logger.info("BIOAF_BASE_WORK_NODE_IMAGE_URI not set; skipping bioaf-base seed")
        return

    org_row = (await session.execute(text("SELECT id FROM organizations LIMIT 1"))).fetchone()
    if not org_row:
        return
    org_id = org_row[0]

    existing = (
        await session.execute(
            select(Environment).where(
                Environment.organization_id == org_id,
                Environment.name == BIOAF_BASE_NAME,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return

    admin_row = (
        await session.execute(
            text(
                "SELECT u.id FROM users u "
                "JOIN roles r ON u.role_id = r.id "
                "WHERE u.organization_id = :org_id AND r.name = 'admin' "
                "ORDER BY u.id LIMIT 1"
            ).bindparams(org_id=org_id)
        )
    ).fetchone()
    if not admin_row:
        return
    user_id = admin_row[0]

    env = Environment(
        name=BIOAF_BASE_NAME,
        description=BIOAF_BASE_DESCRIPTION,
        organization_id=org_id,
        created_by_user_id=user_id,
        visibility="organization",
        environment_type="work_node",
    )
    session.add(env)
    await session.flush()

    version = EnvironmentVersion(
        environment_id=env.id,
        version_number=1,
        build_number=1,
        status="ready",
        definition_format="conda",
        definition_content=BIOAF_BASE_CONDA_YML,
        image_uri=image_uri,
        created_by_user_id=user_id,
    )
    session.add(version)
    await session.flush()

    logger.info(
        "Seeded built-in environment '%s' (id=%d) -> %s",
        env.name,
        env.id,
        image_uri,
    )
