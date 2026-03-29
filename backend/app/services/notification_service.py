"""Notification service - CRUD for notifications, preferences, rules, webhooks."""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import (
    Notification,
    NotificationDeliveryLog,
    NotificationPreference,
    NotificationRule,
    SlackChannelMapping,
    SlackInstallation,
    SlackWebhook,
)
from app.services.notification_channels.email_adapter import EmailChannel
from app.services.notification_channels.slack_adapter import SlackChannel

logger = logging.getLogger("bioaf.notification_service")

SLACK_ERROR_MESSAGES = {
    "not_in_channel": (
        'The bioAF app is not in this channel. Open the channel in Slack and type "@bioAF" to invite it.'
    ),
    "channel_not_found": ("This channel no longer exists in Slack. Remove this mapping and add the correct channel."),
    "invalid_auth": (
        "The Slack connection is no longer valid. Disconnect and reconnect from the Slack Integration page."
    ),
    "token_revoked": (
        "The Slack connection has been revoked. Disconnect and reconnect from the Slack Integration page."
    ),
}


class NotificationService:
    # ---- Notifications CRUD ----

    @staticmethod
    async def list_notifications(
        session: AsyncSession,
        user_id: int,
        read_filter: bool | None = None,
        event_type: str | None = None,
        severity: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Notification], int]:
        query = select(Notification).where(Notification.user_id == user_id)
        count_query = select(func.count()).select_from(Notification).where(Notification.user_id == user_id)

        if read_filter is not None:
            query = query.where(Notification.read == read_filter)
            count_query = count_query.where(Notification.read == read_filter)
        if event_type:
            query = query.where(Notification.event_type == event_type)
            count_query = count_query.where(Notification.event_type == event_type)
        if severity:
            query = query.where(Notification.severity == severity)
            count_query = count_query.where(Notification.severity == severity)

        total_result = await session.execute(count_query)
        total = total_result.scalar() or 0

        query = query.order_by(Notification.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)
        result = await session.execute(query)
        notifications = list(result.scalars().all())

        return notifications, total

    @staticmethod
    async def get_unread_count(session: AsyncSession, user_id: int) -> int:
        result = await session.execute(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.read == False,  # noqa: E712
            )
        )
        return result.scalar() or 0

    @staticmethod
    async def mark_read(session: AsyncSession, notification_id: int, user_id: int) -> Notification | None:
        result = await session.execute(
            select(Notification).where(
                Notification.id == notification_id,
                Notification.user_id == user_id,
            )
        )
        notification = result.scalar_one_or_none()
        if notification:
            notification.read = True
            notification.read_at = datetime.now(timezone.utc)
            await session.flush()
        return notification

    @staticmethod
    async def mark_all_read(session: AsyncSession, user_id: int) -> int:
        result = await session.execute(
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.read == False,  # noqa: E712
            )
            .values(read=True, read_at=datetime.now(timezone.utc))
        )
        await session.flush()
        return result.rowcount

    @staticmethod
    async def delete_notification(session: AsyncSession, notification_id: int, user_id: int) -> bool:
        result = await session.execute(
            select(Notification).where(
                Notification.id == notification_id,
                Notification.user_id == user_id,
            )
        )
        notification = result.scalar_one_or_none()
        if notification:
            await session.delete(notification)
            await session.flush()
            return True
        return False

    # ---- Preferences ----

    @staticmethod
    async def get_preferences(session: AsyncSession, user_id: int) -> list[NotificationPreference]:
        result = await session.execute(select(NotificationPreference).where(NotificationPreference.user_id == user_id))
        return list(result.scalars().all())

    @staticmethod
    async def update_preferences(
        session: AsyncSession,
        user_id: int,
        preferences: list[dict],
    ) -> list[NotificationPreference]:
        # Delete existing preferences for this user
        await session.execute(delete(NotificationPreference).where(NotificationPreference.user_id == user_id))

        # Insert new preferences
        new_prefs = []
        for pref in preferences:
            p = NotificationPreference(
                user_id=user_id,
                event_type=pref["event_type"],
                channel=pref["channel"],
                enabled=pref.get("enabled", True),
            )
            session.add(p)
            new_prefs.append(p)

        await session.flush()
        return new_prefs

    # ---- Rules (admin only) ----

    @staticmethod
    async def get_rules(session: AsyncSession, org_id: int) -> list[NotificationRule]:
        result = await session.execute(select(NotificationRule).where(NotificationRule.organization_id == org_id))
        return list(result.scalars().all())

    @staticmethod
    async def update_rules(
        session: AsyncSession,
        org_id: int,
        rules: list[dict],
    ) -> list[NotificationRule]:
        # Delete existing rules for this org
        await session.execute(delete(NotificationRule).where(NotificationRule.organization_id == org_id))

        new_rules = []
        for rule in rules:
            r = NotificationRule(
                organization_id=org_id,
                event_type=rule["event_type"],
                channel=rule["channel"],
                role_filter=rule.get("role_filter"),
                mandatory=rule.get("mandatory", False),
                enabled=rule.get("enabled", True),
            )
            session.add(r)
            new_rules.append(r)

        await session.flush()
        return new_rules

    # ---- Slack Webhooks (admin only) ----

    @staticmethod
    async def list_webhooks(session: AsyncSession, org_id: int) -> list[SlackWebhook]:
        result = await session.execute(select(SlackWebhook).where(SlackWebhook.organization_id == org_id))
        return list(result.scalars().all())

    @staticmethod
    async def create_webhook(session: AsyncSession, org_id: int, data: dict) -> SlackWebhook:
        webhook = SlackWebhook(
            organization_id=org_id,
            name=data["name"],
            webhook_url=data["webhook_url"],
            channel_name=data.get("channel_name"),
            event_types_json=data.get("event_types", []),
            enabled=data.get("enabled", True),
        )
        session.add(webhook)
        await session.flush()
        return webhook

    @staticmethod
    async def update_webhook(
        session: AsyncSession,
        webhook_id: int,
        org_id: int,
        data: dict,
    ) -> SlackWebhook | None:
        result = await session.execute(
            select(SlackWebhook).where(
                SlackWebhook.id == webhook_id,
                SlackWebhook.organization_id == org_id,
            )
        )
        webhook = result.scalar_one_or_none()
        if not webhook:
            return None

        if "name" in data:
            webhook.name = data["name"]
        if "webhook_url" in data:
            webhook.webhook_url = data["webhook_url"]
        if "channel_name" in data:
            webhook.channel_name = data["channel_name"]
        if "event_types" in data:
            webhook.event_types_json = data["event_types"]
        if "enabled" in data:
            webhook.enabled = data["enabled"]

        await session.flush()
        return webhook

    @staticmethod
    async def delete_webhook(session: AsyncSession, webhook_id: int, org_id: int) -> bool:
        result = await session.execute(
            select(SlackWebhook).where(
                SlackWebhook.id == webhook_id,
                SlackWebhook.organization_id == org_id,
            )
        )
        webhook = result.scalar_one_or_none()
        if webhook:
            await session.delete(webhook)
            await session.flush()
            return True
        return False

    # ---- Test delivery ----

    @staticmethod
    async def test_delivery(
        session: AsyncSession,
        org_id: int,
        channel: str,
    ) -> dict:
        title = "bioAF Test Notification"
        message = "This is a test notification from your bioAF platform."
        severity = "info"

        if channel == "in_app":
            # We need a user to send to, but for testing we skip
            return {"channel": "in_app", "status": "skipped", "detail": "In-app always works"}
        elif channel == "email":
            if not EmailChannel.is_configured():
                return {"channel": "email", "status": "failed", "detail": "SMTP not configured"}
            return {"channel": "email", "status": "configured", "detail": "SMTP is configured"}
        elif channel == "slack":
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
                if not mappings:
                    return {
                        "channel": "slack",
                        "status": "failed",
                        "detail": "Slack connected but no channel mappings configured",
                    }

                results = []
                any_sent = False
                for mapping in mappings:
                    success, error_code = await SlackChannel.deliver(
                        bot_token=install.bot_token,
                        channel_id=mapping.channel_id,
                        title=title,
                        message=message,
                        severity=severity,
                    )
                    if success:
                        any_sent = True
                    detail = ""
                    if not success:
                        detail = SLACK_ERROR_MESSAGES.get(error_code, f"Delivery failed ({error_code})")
                    results.append(
                        {
                            "webhook": mapping.channel_name,
                            "status": "sent" if success else "failed",
                            "detail": detail,
                        }
                    )
                return {
                    "channel": "slack",
                    "status": "sent" if any_sent else "failed",
                    "webhooks": results,
                }

            # Fallback to legacy webhooks
            webhooks_result = await session.execute(
                select(SlackWebhook).where(
                    SlackWebhook.organization_id == org_id,
                    SlackWebhook.enabled == True,  # noqa: E712
                )
            )
            webhooks = list(webhooks_result.scalars().all())
            if not webhooks:
                return {"channel": "slack", "status": "failed", "detail": "Slack not connected"}

            results = []
            for webhook in webhooks:
                success = await SlackChannel.deliver_webhook(
                    webhook_url=webhook.webhook_url,
                    title=title,
                    message=message,
                    severity=severity,
                )
                results.append(
                    {
                        "webhook": webhook.name,
                        "status": "sent" if success else "failed",
                    }
                )
            return {"channel": "slack", "status": "sent", "webhooks": results}

        return {"channel": channel, "status": "unknown_channel"}

    # ---- Retention cleanup ----

    @staticmethod
    async def cleanup_old_notifications(session: AsyncSession) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=90)

        # First delete delivery logs for notifications that will be deleted
        old_notifications = select(Notification.id).where(
            Notification.read == True,  # noqa: E712
            Notification.created_at < cutoff,
        )
        await session.execute(
            delete(NotificationDeliveryLog).where(NotificationDeliveryLog.notification_id.in_(old_notifications))
        )

        result = await session.execute(
            delete(Notification).where(
                Notification.read == True,  # noqa: E712
                Notification.created_at < cutoff,
            )
        )
        count = result.rowcount
        if count > 0:
            logger.info("Cleaned up %d old read notifications", count)
        await session.commit()
        return count
