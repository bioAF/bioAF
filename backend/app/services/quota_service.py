import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.user_quota import UserQuota
from app.services.audit_service import log_action
from app.services.event_bus import event_bus
from app.services.event_types import QUOTA_WARNING

logger = logging.getLogger("bioaf.quotas")


class QuotaService:
    @staticmethod
    async def get_quota(session: AsyncSession, user_id: int) -> UserQuota | None:
        result = await session.execute(
            select(UserQuota).options(selectinload(UserQuota.user)).where(UserQuota.user_id == user_id)
        )
        quota = result.scalar_one_or_none()

        if quota:
            # Check if quota needs reset
            now = datetime.now(timezone.utc)
            if now >= quota.quota_reset_at:
                quota.cpu_hours_used_current_month = Decimal("0")
                quota.quota_reset_at = QuotaService._next_month_boundary(now)
                await session.flush()

        return quota

    @staticmethod
    async def get_or_create_quota(session: AsyncSession, user_id: int, org_id: int) -> UserQuota:
        quota = await QuotaService.get_quota(session, user_id)
        if not quota:
            now = datetime.now(timezone.utc)
            quota = UserQuota(
                user_id=user_id,
                organization_id=org_id,
                cpu_hours_monthly_limit=None,
                cpu_hours_used_current_month=Decimal("0"),
                quota_reset_at=QuotaService._next_month_boundary(now),
            )
            session.add(quota)
            await session.flush()
        return quota

    @staticmethod
    async def check_quota(session: AsyncSession, user_id: int, estimated_hours: float) -> tuple[bool, str]:
        result = await session.execute(select(UserQuota).where(UserQuota.user_id == user_id))
        quota = result.scalar_one_or_none()

        if not quota:
            return True, "No quota set (unlimited)"

        # Check for reset
        now = datetime.now(timezone.utc)
        if now >= quota.quota_reset_at:
            quota.cpu_hours_used_current_month = Decimal("0")
            quota.quota_reset_at = QuotaService._next_month_boundary(now)
            await session.flush()

        if quota.cpu_hours_monthly_limit is None:
            return True, "Unlimited quota"

        projected = float(quota.cpu_hours_used_current_month) + estimated_hours
        if projected > quota.cpu_hours_monthly_limit:
            asyncio.create_task(
                event_bus.emit(
                    QUOTA_WARNING,
                    {
                        "event_type": QUOTA_WARNING,
                        "org_id": quota.organization_id,
                        "user_id": user_id,
                        "target_user_id": user_id,
                        "entity_type": "user_quota",
                        "entity_id": quota.id,
                        "title": "Compute quota exceeded",
                        "message": (
                            f"Used {float(quota.cpu_hours_used_current_month):.1f} of "
                            f"{quota.cpu_hours_monthly_limit} CPU-hours this month"
                        ),
                        "severity": "warning",
                        "summary": f"User quota exceeded ({float(quota.cpu_hours_used_current_month):.1f}/{quota.cpu_hours_monthly_limit} CPU-hours)",
                    },
                )
            )
            await log_action(
                session,
                user_id=user_id,
                entity_type="user_quota",
                entity_id=quota.id,
                action="quota_exceeded",
                details={
                    "cpu_hours_used": float(quota.cpu_hours_used_current_month),
                    "cpu_hours_limit": quota.cpu_hours_monthly_limit,
                    "estimated_hours": estimated_hours,
                    "projected_total": projected,
                },
            )
            await session.flush()
            return False, (
                f"Would exceed monthly limit: {float(quota.cpu_hours_used_current_month):.1f} used "
                f"+ {estimated_hours:.1f} estimated = {projected:.1f} / {quota.cpu_hours_monthly_limit} limit"
            )

        # Warn at 80% usage
        usage_pct = float(quota.cpu_hours_used_current_month) / quota.cpu_hours_monthly_limit
        if usage_pct >= 0.8:
            asyncio.create_task(
                event_bus.emit(
                    QUOTA_WARNING,
                    {
                        "event_type": QUOTA_WARNING,
                        "org_id": quota.organization_id,
                        "user_id": user_id,
                        "target_user_id": user_id,
                        "entity_type": "user_quota",
                        "entity_id": quota.id,
                        "title": f"Compute quota at {usage_pct:.0%}",
                        "message": (
                            f"Used {float(quota.cpu_hours_used_current_month):.1f} of "
                            f"{quota.cpu_hours_monthly_limit} CPU-hours this month"
                        ),
                        "severity": "warning",
                        "summary": f"User quota at {usage_pct:.0%}",
                    },
                )
            )

        return True, "Within quota"

    @staticmethod
    async def update_usage(session: AsyncSession, user_id: int, hours_consumed: float) -> None:
        result = await session.execute(select(UserQuota).where(UserQuota.user_id == user_id))
        quota = result.scalar_one_or_none()
        if quota:
            quota.cpu_hours_used_current_month += Decimal(str(hours_consumed))
            await session.flush()

    @staticmethod
    async def set_quota(
        session: AsyncSession,
        user_id: int,
        admin_user_id: int,
        org_id: int,
        limit: int | None,
    ) -> UserQuota:
        quota = await QuotaService.get_or_create_quota(session, user_id, org_id)

        old_limit = quota.cpu_hours_monthly_limit
        quota.cpu_hours_monthly_limit = limit
        await session.flush()

        await log_action(
            session,
            user_id=admin_user_id,
            entity_type="user_quota",
            entity_id=quota.id,
            action="set_quota",
            details={"target_user_id": user_id, "cpu_hours_monthly_limit": limit},
            previous_value={"cpu_hours_monthly_limit": old_limit},
        )
        return quota

    @staticmethod
    async def list_quotas(session: AsyncSession, org_id: int) -> list[UserQuota]:
        result = await session.execute(
            select(UserQuota)
            .options(selectinload(UserQuota.user))
            .where(UserQuota.organization_id == org_id)
            .order_by(UserQuota.user_id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def reset_monthly_quotas(session: AsyncSession) -> None:
        """Background task: reset quotas that have passed their reset date."""
        try:
            now = datetime.now(timezone.utc)
            result = await session.execute(select(UserQuota).where(UserQuota.quota_reset_at <= now))
            quotas = list(result.scalars().all())

            for quota in quotas:
                quota.cpu_hours_used_current_month = Decimal("0")
                quota.quota_reset_at = QuotaService._next_month_boundary(now)

            await session.flush()
            await session.commit()

            if quotas:
                logger.info("Reset monthly quotas for %d users", len(quotas))
        except Exception as e:
            logger.error("Monthly quota reset failed: %s", e)

    @staticmethod
    def _next_month_boundary(now: datetime) -> datetime:
        """Return the first moment of next month."""
        if now.month == 12:
            return datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
        return datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
