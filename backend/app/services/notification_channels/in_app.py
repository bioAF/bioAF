"""In-app notification channel - writes to the notifications table."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification, NotificationPreference

logger = logging.getLogger("bioaf.notifications.in_app")


class InAppChannel:
    @staticmethod
    async def deliver(
        session: AsyncSession,
        org_id: int,
        user_id: int,
        event_type: str,
        title: str,
        message: str,
        severity: str,
        metadata: dict | None = None,
    ) -> Notification | None:
        """Create the in-app notification unless the user has disabled the in-app channel for this
        event type (default is on when no preference is set). Returns None when suppressed, so every
        caller - the router and the direct callers - honors the user's UI toggle uniformly."""
        pref = (
            await session.execute(
                select(NotificationPreference).where(
                    NotificationPreference.user_id == user_id,
                    NotificationPreference.event_type == event_type,
                    NotificationPreference.channel == "in_app",
                )
            )
        ).scalar_one_or_none()
        if pref is not None and not pref.enabled:
            logger.info("In-app notification suppressed by preference for user %d: %s", user_id, event_type)
            return None

        notification = Notification(
            organization_id=org_id,
            user_id=user_id,
            event_type=event_type,
            title=title,
            message=message,
            severity=severity,
            metadata_json=metadata or {},
        )
        session.add(notification)
        await session.flush()
        logger.info("In-app notification created for user %d: %s", user_id, title)
        return notification
