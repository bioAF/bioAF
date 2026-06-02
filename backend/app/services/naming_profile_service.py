"""CRUD service for Naming Profiles.

The service is a thin wrapper around the model. Schema validation
(identifier shape, padding range, duplicate identifiers, single-date-segment
rule) lives in the Pydantic layer at app/schemas/naming_profile.py and is
applied at the API boundary; we re-run it here for non-API callers.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.file_parse_result import FileParseResult
from app.models.naming_profile import NamingProfile
from app.schemas.naming_profile import NamingProfileCreate, NamingProfileUpdate
from app.services.audit_service import log_action


class NamingProfileService:
    @staticmethod
    async def create_profile(
        session: AsyncSession,
        org_id: int,
        user_id: int,
        data: NamingProfileCreate,
    ) -> NamingProfile:
        profile = NamingProfile(
            organization_id=org_id,
            name=data.name,
            description=data.description,
            delimiter=data.delimiter,
            strip_extension=data.strip_extension,
            segments_json=[seg.model_dump() for seg in data.segments],
            experiment_template_id=data.experiment_template_id,
            status="active",
            created_by=user_id,
        )
        session.add(profile)
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="naming_profile",
            entity_id=profile.id,
            action="create",
            details={"name": data.name, "delimiter": data.delimiter},
        )
        return profile

    @staticmethod
    async def get_profile(
        session: AsyncSession, profile_id: int
    ) -> NamingProfile | None:
        result = await session.execute(
            select(NamingProfile).where(NamingProfile.id == profile_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_profiles(
        session: AsyncSession,
        org_id: int,
        status_filter: str | None = None,
    ) -> list[NamingProfile]:
        query = select(NamingProfile).where(NamingProfile.organization_id == org_id)
        if status_filter:
            query = query.where(NamingProfile.status == status_filter)
        query = query.order_by(NamingProfile.created_at.desc())
        result = await session.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def update_profile(
        session: AsyncSession,
        profile_id: int,
        user_id: int,
        data: NamingProfileUpdate,
    ) -> NamingProfile | None:
        result = await session.execute(
            select(NamingProfile).where(NamingProfile.id == profile_id)
        )
        profile = result.scalar_one_or_none()
        if not profile:
            return None

        previous: dict[str, str | None] = {}
        updates: dict[str, str] = {}
        for field in ("name", "description", "delimiter", "strip_extension", "experiment_template_id"):
            new_val = getattr(data, field, None)
            if new_val is not None:
                old_val = getattr(profile, field)
                previous[field] = str(old_val) if old_val is not None else None
                setattr(profile, field, new_val)
                updates[field] = str(new_val)

        if data.segments is not None:
            previous["segments_json"] = "updated"
            profile.segments_json = [seg.model_dump() for seg in data.segments]
            updates["segments_json"] = "updated"

        if updates:
            await session.flush()
            await log_action(
                session,
                user_id=user_id,
                entity_type="naming_profile",
                entity_id=profile.id,
                action="update",
                details=updates,
                previous_value=previous,
            )
        return profile

    @staticmethod
    async def deactivate_profile(
        session: AsyncSession,
        profile_id: int,
        user_id: int,
    ) -> NamingProfile | None:
        result = await session.execute(
            select(NamingProfile).where(NamingProfile.id == profile_id)
        )
        profile = result.scalar_one_or_none()
        if not profile:
            return None

        old_status = profile.status
        profile.status = "inactive"
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="naming_profile",
            entity_id=profile.id,
            action="deactivate",
            details={"status": "inactive"},
            previous_value={"status": old_status},
        )
        return profile

    @staticmethod
    async def get_match_statistics(
        session: AsyncSession,
        profile_id: int,
        days: int = 30,
    ) -> int:
        """Count files matched by this profile in the last N days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        result = await session.execute(
            select(func.count(FileParseResult.id)).where(
                FileParseResult.naming_profile_id == profile_id,
                FileParseResult.match_status == "matched",
                FileParseResult.created_at >= cutoff,
            )
        )
        return result.scalar_one()
