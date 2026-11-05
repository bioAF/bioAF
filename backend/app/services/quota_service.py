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
from app.services.post_commit_events import defer_event

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
    async def check_quota(
        session: AsyncSession, user_id: int, estimated_hours: float, *, isolate_denial: bool = False
    ) -> tuple[bool, str]:
        """Whether ``user_id`` may spend ``estimated_hours`` more CPU-hours this month.

        A denial is audited (``quota_exceeded``) and announced (QUOTA_WARNING). With
        ``isolate_denial`` the audit is committed on a session of its own and the warning follows
        that commit, so the record survives the caller rolling its request back; nothing pending on
        ``session`` is committed. Pipeline launches use it. Without it the audit joins the caller's
        transaction, which a caller that raises on denial rolls back.
        """
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
            used = float(quota.cpu_hours_used_current_month)
            limit = quota.cpu_hours_monthly_limit
            warning = {
                "event_type": QUOTA_WARNING,
                "org_id": quota.organization_id,
                "user_id": user_id,
                "target_user_id": user_id,
                "entity_type": "user_quota",
                "entity_id": quota.id,
                "title": "Compute quota exceeded",
                "message": f"Used {used:.1f} of {limit} CPU-hours this month",
                "severity": "warning",
                "summary": f"User quota exceeded ({used:.1f}/{limit} CPU-hours)",
            }
            audit_details = {
                "cpu_hours_used": used,
                "cpu_hours_limit": limit,
                "estimated_hours": estimated_hours,
                "projected_total": projected,
            }
            if isolate_denial:
                await QuotaService._record_denial_isolated(user_id, quota.id, audit_details, warning)
            else:
                asyncio.create_task(event_bus.emit(QUOTA_WARNING, warning))
                await log_action(
                    session,
                    user_id=user_id,
                    entity_type="user_quota",
                    entity_id=quota.id,
                    action="quota_exceeded",
                    details=audit_details,
                )
                await session.flush()
            return False, (
                f"Would exceed monthly limit: {used:.1f} used "
                f"+ {estimated_hours:.1f} estimated = {projected:.1f} / {limit} limit"
            )

        # Warn at 80% usage
        usage_pct = float(quota.cpu_hours_used_current_month) / quota.cpu_hours_monthly_limit
        if usage_pct >= 0.8:
            # Announced once the caller commits whatever it was checking the quota for.
            defer_event(
                session,
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

        return True, "Within quota"

    @staticmethod
    async def _record_denial_isolated(user_id: int, quota_id: int, details: dict, warning: dict) -> None:
        """Commit a ``quota_exceeded`` audit on its own session, then announce the warning.

        Looked up through the module at call time, not imported by name, so a swapped database (and
        the test engine) is the one written to. The request's API-key attribution travels in the
        context variable ``log_action`` reads. If this commit fails the denial still stands, nothing
        is announced, and the failure is logged: the launch is refused either way.
        """
        import app.database as database

        try:
            async with database.async_session_factory() as audit_session:
                await log_action(
                    audit_session,
                    user_id=user_id,
                    entity_type="user_quota",
                    entity_id=quota_id,
                    action="quota_exceeded",
                    details=details,
                )
                defer_event(audit_session, QUOTA_WARNING, warning)
                await audit_session.commit()
        except Exception:
            logger.exception("Could not record quota denial for user %d (quota %d)", user_id, quota_id)

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
