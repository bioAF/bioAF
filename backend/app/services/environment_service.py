import logging

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.environment import Environment
from app.models.environment_version import EnvironmentVersion
from app.services.audit_service import log_action

logger = logging.getLogger("bioaf.environment")

# Default conda environment.yml for work nodes.  Provides a practical
# starting point that scientists can extend with their own packages.
DEFAULT_WORK_NODE_CONDA_YML = """\
name: bioaf
channels:
  - conda-forge
  - bioconda
dependencies:
  - python=3.11
  - numpy
  - pandas
  - scipy
  - matplotlib
  - seaborn
  - jupyter
  - ipython
  - scikit-learn
  - pip
"""

VALID_VISIBILITIES = ("team", "organization")
VALID_DEFINITION_FORMATS = ("dockerfile", "conda")
VALID_ENVIRONMENT_TYPES = ("notebook", "work_node", "pipeline")

# Default conda environment.yml for pipeline environments.  Provides a
# practical starting point for Nextflow/nf-core wrapper code.
DEFAULT_PIPELINE_CONDA_YML = """\
name: bioaf-pipeline
channels:
  - conda-forge
  - bioconda
dependencies:
  - python=3.11
  - numpy
  - pandas
  - scipy
  - matplotlib
  - scikit-learn
  - pip
"""


class EnvironmentService:
    @staticmethod
    async def list_environments(
        session: AsyncSession,
        org_id: int,
        environment_type: str | None = None,
    ) -> list[Environment]:
        """List environments within an organization, optionally filtered by type."""
        query = select(Environment).where(Environment.organization_id == org_id)
        if environment_type:
            query = query.where(Environment.environment_type == environment_type)
        query = query.order_by(Environment.created_at.desc())
        result = await session.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def get_environment(session: AsyncSession, org_id: int, environment_id: int) -> Environment | None:
        result = await session.execute(
            select(Environment).where(
                Environment.id == environment_id,
                Environment.organization_id == org_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def create_environment(
        session: AsyncSession,
        org_id: int,
        user_id: int,
        name: str,
        description: str | None = None,
        visibility: str = "team",
        environment_type: str = "notebook",
    ) -> Environment:
        if visibility not in VALID_VISIBILITIES:
            raise ValueError(f"Invalid visibility: {visibility}")
        if environment_type not in VALID_ENVIRONMENT_TYPES:
            raise ValueError(f"Invalid environment_type: {environment_type}")

        # Check for duplicate name within org
        existing = await session.execute(
            select(Environment).where(
                Environment.organization_id == org_id,
                Environment.name == name,
            )
        )
        if existing.scalar_one_or_none():
            raise ValueError(f"Environment '{name}' already exists")

        env = Environment(
            name=name,
            description=description,
            organization_id=org_id,
            created_by_user_id=user_id,
            visibility=visibility,
            environment_type=environment_type,
        )
        session.add(env)
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="environment",
            entity_id=env.id,
            action="create",
            details={"name": name, "visibility": visibility},
        )
        return env

    @staticmethod
    async def update_environment(
        session: AsyncSession,
        org_id: int,
        environment_id: int,
        name: str | None = None,
        description: str | None = None,
        visibility: str | None = None,
    ) -> Environment:
        env = await EnvironmentService.get_environment(session, org_id, environment_id)
        if not env:
            raise ValueError("Environment not found")

        if visibility and visibility not in VALID_VISIBILITIES:
            raise ValueError(f"Invalid visibility: {visibility}")

        if name is not None:
            existing = await session.execute(
                select(Environment).where(
                    Environment.organization_id == org_id,
                    Environment.name == name,
                    Environment.id != environment_id,
                )
            )
            if existing.scalar_one_or_none():
                raise ValueError(f"Environment '{name}' already exists")
            env.name = name

        if description is not None:
            env.description = description
        if visibility is not None:
            env.visibility = visibility

        await session.flush()
        return env

    @staticmethod
    async def delete_environment(session: AsyncSession, org_id: int, user_id: int, environment_id: int) -> None:
        env = await EnvironmentService.get_environment(session, org_id, environment_id)
        if not env:
            raise ValueError("Environment not found")

        await log_action(
            session,
            user_id=user_id,
            entity_type="environment",
            entity_id=env.id,
            action="delete",
            details={"name": env.name},
        )

        # Delete versions first
        versions_result = await session.execute(
            select(EnvironmentVersion).where(EnvironmentVersion.environment_id == environment_id)
        )
        for v in versions_result.scalars().all():
            await session.delete(v)

        await session.delete(env)
        await session.flush()

    # --- Version methods ---

    @staticmethod
    async def create_version(
        session: AsyncSession,
        org_id: int,
        user_id: int,
        environment_id: int,
        definition_format: str,
        definition_content: str,
    ) -> EnvironmentVersion:
        env = await EnvironmentService.get_environment(session, org_id, environment_id)
        if not env:
            raise ValueError("Environment not found")

        if definition_format not in VALID_DEFINITION_FORMATS:
            raise ValueError(f"Invalid definition_format: {definition_format}")

        # Work node environments only support conda (ADR-043)
        if env.environment_type == "work_node" and definition_format != "conda":
            raise ValueError("Work node environments only support conda definition format")

        # Pipeline environments only support conda (ADR-045)
        if env.environment_type == "pipeline" and definition_format != "conda":
            raise ValueError("Pipeline environments only support conda definition format")

        # Auto-increment version number
        result = await session.execute(
            select(func.coalesce(func.max(EnvironmentVersion.version_number), 0)).where(
                EnvironmentVersion.environment_id == environment_id
            )
        )
        max_version = result.scalar() or 0
        next_version = max_version + 1

        version = EnvironmentVersion(
            environment_id=environment_id,
            version_number=next_version,
            status="draft",
            definition_format=definition_format,
            definition_content=definition_content,
            created_by_user_id=user_id,
        )
        session.add(version)
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="environment_version",
            entity_id=version.id,
            action="create",
            details={
                "environment_id": environment_id,
                "version_number": next_version,
                "definition_format": definition_format,
            },
        )
        return version

    @staticmethod
    async def get_version(
        session: AsyncSession, org_id: int, environment_id: int, version_id: int
    ) -> EnvironmentVersion | None:
        env = await EnvironmentService.get_environment(session, org_id, environment_id)
        if not env:
            return None

        result = await session.execute(
            select(EnvironmentVersion).where(
                EnvironmentVersion.id == version_id,
                EnvironmentVersion.environment_id == environment_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_versions(session: AsyncSession, environment_id: int) -> list[EnvironmentVersion]:
        result = await session.execute(
            select(EnvironmentVersion)
            .where(EnvironmentVersion.environment_id == environment_id)
            .order_by(EnvironmentVersion.version_number.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def delete_version(session: AsyncSession, org_id: int, environment_id: int, version_id: int) -> None:
        """Delete a single environment version."""
        version = await EnvironmentService.get_version(session, org_id, environment_id, version_id)
        if not version:
            raise ValueError("Version not found")

        await session.delete(version)
        await session.flush()

    @staticmethod
    async def rebuild_version(
        session: AsyncSession,
        org_id: int,
        user_id: int,
        environment_id: int,
        version_id: int,
    ) -> EnvironmentVersion:
        """Create a new build of an existing version (v1.1 -> v1.2)."""
        env = await EnvironmentService.get_environment(session, org_id, environment_id)
        if not env:
            raise ValueError("Environment not found")

        original = await EnvironmentService.get_version(session, org_id, environment_id, version_id)
        if not original:
            raise ValueError("Version not found")

        # Find the max build_number for this version_number
        result = await session.execute(
            select(func.coalesce(func.max(EnvironmentVersion.build_number), 0)).where(
                EnvironmentVersion.environment_id == environment_id,
                EnvironmentVersion.version_number == original.version_number,
            )
        )
        max_build = result.scalar() or 0
        next_build = max_build + 1

        rebuild = EnvironmentVersion(
            environment_id=environment_id,
            version_number=original.version_number,
            build_number=next_build,
            status="draft",
            definition_format=original.definition_format,
            definition_content=original.definition_content,
            created_by_user_id=user_id,
        )
        session.add(rebuild)
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="environment_version",
            entity_id=rebuild.id,
            action="rebuild",
            details={
                "environment_id": environment_id,
                "version_number": original.version_number,
                "build_number": next_build,
                "original_version_id": version_id,
            },
        )
        return rebuild

    @staticmethod
    async def get_in_progress_builds(session: AsyncSession) -> list[EnvironmentVersion]:
        """Get all versions currently in 'building' status."""
        result = await session.execute(select(EnvironmentVersion).where(EnvironmentVersion.status == "building"))
        return list(result.scalars().all())


async def ensure_default_work_node_environment(session: AsyncSession) -> None:
    """Create a default work node environment if none exists yet.

    Called at startup so users always have a base conda environment to
    build on for work nodes.  The version is created in 'draft' status --
    the user must trigger a build before it can be used.
    """
    # Get the org (single-tenant)
    row = (await session.execute(text("SELECT id FROM organizations LIMIT 1"))).fetchone()
    if not row:
        return
    org_id = row[0]

    # Skip if a work_node environment already exists
    existing = (
        await session.execute(
            text(
                "SELECT id FROM environments WHERE organization_id = :org_id AND environment_type = 'work_node' LIMIT 1"
            ).bindparams(org_id=org_id)
        )
    ).fetchone()
    if existing:
        return

    # Get the first admin user to attribute creation to
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
        name="Default Work Node",
        description="Base Python environment for work nodes. Build this environment, then customize with your own packages.",
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
        status="draft",
        definition_format="conda",
        definition_content=DEFAULT_WORK_NODE_CONDA_YML,
        created_by_user_id=user_id,
    )
    session.add(version)
    await session.flush()

    logger.info("Created default work node environment '%s' (id=%d)", env.name, env.id)


async def ensure_default_pipeline_environment(session: AsyncSession) -> None:
    """Create a default pipeline environment if none exists yet.

    Called at startup so users always have a base conda environment to
    build on for custom pipelines.  The version is created in 'draft'
    status -- the user must trigger a build before it can be used.
    """
    # Get the org (single-tenant)
    row = (await session.execute(text("SELECT id FROM organizations LIMIT 1"))).fetchone()
    if not row:
        return
    org_id = row[0]

    # Skip if a pipeline environment already exists
    existing = (
        await session.execute(
            text(
                "SELECT id FROM environments WHERE organization_id = :org_id AND environment_type = 'pipeline' LIMIT 1"
            ).bindparams(org_id=org_id)
        )
    ).fetchone()
    if existing:
        return

    # Get the first admin user to attribute creation to
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
        name="Base Pipeline Environment",
        description="Base Python environment for custom pipelines. Build this environment, then customize with your own packages.",
        organization_id=org_id,
        created_by_user_id=user_id,
        visibility="organization",
        environment_type="pipeline",
    )
    session.add(env)
    await session.flush()

    version = EnvironmentVersion(
        environment_id=env.id,
        version_number=1,
        build_number=1,
        status="draft",
        definition_format="conda",
        definition_content=DEFAULT_PIPELINE_CONDA_YML,
        created_by_user_id=user_id,
    )
    session.add(version)
    await session.flush()

    logger.info("Created default pipeline environment '%s' (id=%d)", env.name, env.id)
