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
    SlackChannelMapping,
    SlackInstallation,
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

# Channels that deliver when the user has expressed no preference for an event type.
#
# In-app is the platform's default surface (the bell), so it is on unless the user turns it off.
# Email is OPT-IN: defaulting it on mails every user for every one of the ~40 event types, and
# recipient resolution fans out to all org admins when no rule exists, so a silent default-on is a
# mail flood nobody asked for. Slack only reaches this check from inside a rule loop, so it is
# already gated by the org rule that names the channel.
#
# The profile UI renders the same defaults per channel (frontend NotificationsTab CHANNELS.defaultOn);
# the two must agree or the toggles lie about what will be delivered.
DEFAULT_ON_CHANNELS = {"in_app", "slack"}


def build_notification_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    """Compose the metadata persisted on an in-app notification.

    The frontend deep-links a notification to its associated item using the
    entity reference, so every notification carries ``entity_type`` and
    ``entity_id`` in ``metadata_json``. Any explicit ``metadata`` on the event
    is preserved and takes precedence over the top-level entity reference.
    """
    metadata = dict(payload.get("metadata") or {})
    entity_type = payload.get("entity_type")
    entity_id = payload.get("entity_id")
    if entity_type is not None:
        metadata.setdefault("entity_type", entity_type)
    if entity_id is not None:
        metadata.setdefault("entity_id", entity_id)
    return metadata


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
        # Notifications deep-link to their associated item, so persist the entity
        # reference alongside any explicit metadata (the activity feed keeps its
        # own entity_type/entity_id columns and uses the raw metadata).
        notification_metadata = build_notification_metadata(payload)

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

            # Deliver to each recipient via each channel. In-app and email are preference-driven per
            # user (the toggles the UI writes); slack stays rule/OAuth-driven (org-level channels).
            slack_delivered_via_rule = False
            first_notification_id = None
            for recipient_user in recipients:
                # In-app honors the user's in-app preference (InAppChannel returns None when disabled).
                notification = await InAppChannel.deliver(
                    session=session,
                    org_id=org_id,
                    user_id=recipient_user.id,
                    event_type=event_type,
                    title=title,
                    message=message,
                    severity=severity,
                    metadata=notification_metadata,
                )
                notification_id = notification.id if notification is not None else None
                if notification is not None and first_notification_id is None:
                    first_notification_id = notification.id

                # Email: driven purely by the user's preference (default on) + a mandatory rule
                # override, delivered via the configured SMTP. No per-org rule is required.
                if await self._channel_enabled(session, recipient_user.id, event_type, "email", rules):
                    success = await EmailChannel.deliver(
                        to=recipient_user.email,
                        title=title,
                        message=message,
                        severity=severity,
                    )
                    await self._log_delivery(
                        session, notification_id, "email", "sent" if success else "failed"
                    )

                # Slack via explicit rules (role filter + preference/mandatory), unchanged.
                for rule in rules:
                    if rule.channel != "slack":
                        continue
                    if rule.role_filter:
                        from app.services import role_service

                        user_role = await role_service.get_role_by_id(session, recipient_user.role_id)
                        if not user_role or user_role.name != rule.role_filter:
                            continue
                    if not rule.mandatory:
                        if not await self._check_preference(session, recipient_user.id, event_type, "slack"):
                            continue
                    slack_delivered_via_rule = True
                    await self._deliver_slack(
                        session, org_id, event_type, notification_id, title, message, severity
                    )

            # Deliver to Slack via OAuth channel mappings (independent of rules)
            if not slack_delivered_via_rule and first_notification_id is not None:
                await self._deliver_slack(
                    session,
                    org_id,
                    event_type,
                    first_notification_id,
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

    async def _channel_enabled(
        self,
        session: AsyncSession,
        user_id: int,
        event_type: str,
        channel: str,
        rules: list[NotificationRule],
    ) -> bool:
        """Whether to deliver ``channel`` to this user for this event: a mandatory enabled org rule
        forces it; otherwise it follows the user's preference (default on when unset)."""
        for rule in rules:
            if rule.channel == channel and rule.enabled and rule.mandatory:
                return True
        return await self._check_preference(session, user_id, event_type, channel)

    async def _check_preference(
        self,
        session: AsyncSession,
        user_id: int,
        event_type: str,
        channel: str,
    ) -> bool:
        """Check if user has opted in/out for this event+channel.

        With no stored preference the answer is the channel's default (see DEFAULT_ON_CHANNELS):
        in-app delivers, email does not.
        """
        result = await session.execute(
            select(NotificationPreference).where(
                NotificationPreference.user_id == user_id,
                NotificationPreference.event_type == event_type,
                NotificationPreference.channel == channel,
            )
        )
        pref = result.scalar_one_or_none()
        if pref is None:
            return channel in DEFAULT_ON_CHANNELS
        return pref.enabled

    async def _deliver_slack(
        self,
        session: AsyncSession,
        org_id: int,
        event_type: str,
        notification_id: int | None,
        title: str,
        message: str,
        severity: str,
    ) -> None:
        """Deliver via OAuth bot token (preferred) or legacy webhooks (fallback)."""
        # Try OAuth installation first
        install_result = await session.execute(
            select(SlackInstallation).where(
                SlackInstallation.organization_id == org_id,
                SlackInstallation.enabled == True,  # noqa: E712
            )
        )
        install = install_result.scalar_one_or_none()

        if install:
            mappings_result = await session.execute(
                select(SlackChannelMapping).where(
                    SlackChannelMapping.organization_id == org_id,
                    SlackChannelMapping.enabled == True,  # noqa: E712
                )
            )
            mappings = list(mappings_result.scalars().all())

            for mapping in mappings:
                if mapping.event_types_json and event_type not in mapping.event_types_json:
                    continue

                success, _error = await SlackChannel.deliver(
                    bot_token=install.bot_token,
                    channel_id=mapping.channel_id,
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
            return

        # Fallback to legacy webhooks
        result = await session.execute(
            select(SlackWebhook).where(
                SlackWebhook.organization_id == org_id,
                SlackWebhook.enabled == True,  # noqa: E712
            )
        )
        webhooks = list(result.scalars().all())

        for webhook in webhooks:
            if webhook.event_types_json and event_type not in webhook.event_types_json:
                continue

            success = await SlackChannel.deliver_webhook(
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
        notification_id: int | None,
        channel: str,
        status: str,
    ) -> None:
        # The delivery log is anchored to an in-app notification; when the user suppressed in-app but
        # still gets email/slack, there is no row to anchor to, so skip the log (the channel adapters
        # still log the send to the app log). Avoids a schema change to make the FK nullable.
        if notification_id is None:
            return
        log = NotificationDeliveryLog(
            notification_id=notification_id,
            channel=channel,
            status=status,
            attempts=1,
            last_attempt_at=datetime.now(timezone.utc),
        )
        session.add(log)
        await session.flush()
