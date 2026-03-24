import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.environment import Environment
from app.models.environment_version import EnvironmentVersion
from app.services.audit_service import log_action

logger = logging.getLogger("bioaf.environment")

VALID_VISIBILITIES = ("team", "organization")
VALID_DEFINITION_FORMATS = ("dockerfile", "conda")


class EnvironmentService:
    @staticmethod
    async def list_environments(session: AsyncSession, org_id: int) -> list[Environment]:
        """List environments within an organization."""
        result = await session.execute(
            select(Environment).where(Environment.organization_id == org_id).order_by(Environment.created_at.desc())
        )
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
    ) -> Environment:
        if visibility not in VALID_VISIBILITIES:
            raise ValueError(f"Invalid visibility: {visibility}")

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
    async def get_in_progress_builds(session: AsyncSession) -> list[EnvironmentVersion]:
        """Get all versions currently in 'building' status."""
        result = await session.execute(select(EnvironmentVersion).where(EnvironmentVersion.status == "building"))
        return list(result.scalars().all())
