"""Notification router - subscribes to events, resolves recipients, dispatches to channels."""

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import (
    NotificationDeliveryLog,
    NotificationPreference,
    NotificationRule,
    SlackWebhook,
)
from app.models.activity_feed import ActivityFeedEntry
from app.models.user import User
from app.services.event_bus import event_bus
from app.services.event_types import ALL_EVENT_TYPES, EVENT_SEVERITY
from app.services.notification_channels.in_app import InAppChannel
from app.services.notification_channels.email_adapter import EmailChannel
from app.services.notification_channels.slack_adapter import SlackChannel

logger = logging.getLogger("bioaf.notification_router")


class NotificationRouter:
    """Routes platform events to notification channels based on rules and preferences."""

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    def register(self) -> None:
        for event_type in ALL_EVENT_TYPES:
            event_bus.subscribe(event_type, self._handle_event)
        logger.info("Notification router registered for %d event types", len(ALL_EVENT_TYPES))

    async def _handle_event(self, payload: dict[str, Any]) -> None:
        event_type = payload.get("event_type", "")
        org_id = payload.get("org_id")
        if not org_id:
            logger.warning("Event %s missing org_id, skipping", event_type)
            return

        title = payload.get("title", event_type)
        message = payload.get("message", "")
        severity = payload.get("severity", EVENT_SEVERITY.get(event_type, "info"))
        metadata = payload.get("metadata", {})
        user_id = payload.get("user_id")
        entity_type = payload.get("entity_type")
        entity_id = payload.get("entity_id")
        summary = payload.get("summary", title)

        async with self._session_factory() as session:
            # Write activity feed entry
            feed_entry = ActivityFeedEntry(
                organization_id=org_id,
                user_id=user_id,
                event_type=event_type,
                entity_type=entity_type,
                entity_id=entity_id,
                summary=summary,
                metadata_json=metadata,
            )
            session.add(feed_entry)

            # Get notification rules for this event type and org
            rules_result = await session.execute(
                select(NotificationRule).where(
                    NotificationRule.organization_id == org_id,
                    NotificationRule.event_type == event_type,
                    NotificationRule.enabled == True,  # noqa: E712
                )
            )
            rules = list(rules_result.scalars().all())

            # Resolve recipients
            recipients = await self._resolve_recipients(session, org_id, rules, payload)

            # Deliver to each recipient via each channel
            for recipient_user in recipients:
                # In-app always delivered
                notification = await InAppChannel.deliver(
                    session=session,
                    org_id=org_id,
                    user_id=recipient_user.id,
                    event_type=event_type,
                    title=title,
                    message=message,
                    severity=severity,
                    metadata=metadata,
                )

                # Check email/slack delivery per rules and preferences
                for rule in rules:
                    if rule.channel == "in_app":
                        continue
                    if rule.role_filter:
                        from app.services import role_service

                        user_role = await role_service.get_role_by_id(session, recipient_user.role_id)
                        if not user_role or user_role.name != rule.role_filter:
                            continue

                    # Check user preference (unless mandatory)
                    if not rule.mandatory:
                        pref_enabled = await self._check_preference(
                            session, recipient_user.id, event_type, rule.channel
                        )
                        if not pref_enabled:
                            continue

                    if rule.channel == "email":
                        success = await EmailChannel.deliver(
                            to=recipient_user.email,
                            title=title,
                            message=message,
                            severity=severity,
                        )
                        await self._log_delivery(
                            session,
                            notification.id,
                            "email",
                            "sent" if success else "failed",
                        )
                    elif rule.channel == "slack":
                        await self._deliver_slack(
                            session,
                            org_id,
                            event_type,
                            notification.id,
                            title,
                            message,
                            severity,
                        )

            await session.commit()

    async def _resolve_recipients(
        self,
        session: AsyncSession,
        org_id: int,
        rules: list[NotificationRule],
        payload: dict[str, Any],
    ) -> list[User]:
        """Resolve unique set of users who should receive the notification."""
        role_filters = set()
        for rule in rules:
            if rule.role_filter:
                role_filters.add(rule.role_filter)

        # If no rules exist, default to delivering to admins
        if not rules:
            role_filters = {"admin"}

        # Start with users matching role filters
        query = select(User).where(
            User.organization_id == org_id,
            User.status == "active",
        )
        if role_filters:
            from app.models.role import Role

            query = query.join(Role, User.role_id == Role.id).where(Role.name.in_(role_filters))

        result = await session.execute(query)
        recipients = list(result.scalars().all())

        # Also include the specific user from the payload if provided
        target_user_id = payload.get("target_user_id")
        if target_user_id:
            existing_ids = {u.id for u in recipients}
            if target_user_id not in existing_ids:
                user_result = await session.execute(select(User).where(User.id == target_user_id))
                target_user = user_result.scalar_one_or_none()
                if target_user:
                    recipients.append(target_user)

        return recipients

    async def _check_preference(
        self,
        session: AsyncSession,
        user_id: int,
        event_type: str,
        channel: str,
    ) -> bool:
        """Check if user has opted in/out for this event+channel. Default is enabled."""
        result = await session.execute(
            select(NotificationPreference).where(
                NotificationPreference.user_id == user_id,
                NotificationPreference.event_type == event_type,
                NotificationPreference.channel == channel,
            )
        )
        pref = result.scalar_one_or_none()
        if pref is None:
            return True  # default enabled
        return pref.enabled

    async def _deliver_slack(
        self,
        session: AsyncSession,
        org_id: int,
        event_type: str,
        notification_id: int,
        title: str,
        message: str,
        severity: str,
    ) -> None:
        """Deliver to all matching Slack webhooks for the org."""
        result = await session.execute(
            select(SlackWebhook).where(
                SlackWebhook.organization_id == org_id,
                SlackWebhook.enabled == True,  # noqa: E712
            )
        )
        webhooks = list(result.scalars().all())

        for webhook in webhooks:
            # Check event type filter
            if webhook.event_types_json and event_type not in webhook.event_types_json:
                continue

            success = await SlackChannel.deliver(
                webhook_url=webhook.webhook_url,
                title=title,
                message=message,
                severity=severity,
            )
            await self._log_delivery(
                session,
                notification_id,
                "slack",
                "sent" if success else "failed",
            )

    async def _log_delivery(
        self,
        session: AsyncSession,
        notification_id: int,
        channel: str,
        status: str,
    ) -> None:
        log = NotificationDeliveryLog(
            notification_id=notification_id,
            channel=channel,
            status=status,
            attempts=1,
            last_attempt_at=datetime.now(timezone.utc),
        )
        session.add(log)
        await session.flush()
