"""Slack notification channel adapter supporting bot token (chat.postMessage) and legacy webhooks."""

import asyncio
import logging

import httpx

logger = logging.getLogger("bioaf.notifications.slack")

MAX_RETRIES = 3
BACKOFF_BASE = 2  # seconds: 2, 8, 32

SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"

SEVERITY_COLORS = {
    "info": "#36a64f",  # green
    "warning": "#f2c744",  # yellow
    "critical": "#e01e5a",  # red
}


def _build_blocks(title: str, message: str, severity: str) -> list[dict]:
    """Build Slack Block Kit blocks for a notification."""
    color = SEVERITY_COLORS.get(severity, "#36a64f")
    return [
        {
            "color": color,
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*{title}*"},
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": message},
                },
                {
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": f"Severity: *{severity.capitalize()}*"},
                        {"type": "mrkdwn", "text": "bioAF Platform"},
                    ],
                },
            ],
        }
    ]


class SlackChannel:
    @staticmethod
    async def deliver(
        bot_token: str,
        channel_id: str,
        title: str,
        message: str,
        severity: str,
    ) -> tuple[bool, str]:
        """Send a notification via Slack's chat.postMessage API.

        Returns (success, error_code). error_code is empty on success.
        """
        payload = {
            "channel": channel_id,
            "text": f"{title}: {message}",
            "attachments": _build_blocks(title, message, severity),
        }

        last_error = ""
        for attempt in range(MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(
                        SLACK_POST_MESSAGE_URL,
                        json=payload,
                        headers={"Authorization": f"Bearer {bot_token}"},
                    )
                    data = response.json()
                    if data.get("ok"):
                        logger.info("Slack notification sent to %s: %s", channel_id, title)
                        return True, ""
                    last_error = data.get("error", "unknown_error")
                    logger.warning("Slack API error: %s", last_error)
                    # Do not retry on auth/channel errors
                    if last_error in (
                        "channel_not_found",
                        "not_in_channel",
                        "invalid_auth",
                        "token_revoked",
                    ):
                        return False, last_error
            except Exception as e:
                last_error = str(e)
                backoff = BACKOFF_BASE ** (attempt * 2 + 1)
                logger.warning(
                    "Slack attempt %d/%d failed: %s (backoff %ds)",
                    attempt + 1,
                    MAX_RETRIES,
                    e,
                    backoff,
                )
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(backoff)

        logger.error("Slack delivery failed after %d attempts", MAX_RETRIES)
        return False, last_error

    @staticmethod
    async def deliver_webhook(
        webhook_url: str,
        title: str,
        message: str,
        severity: str,
    ) -> bool:
        """Legacy webhook delivery for backward compatibility."""
        color = SEVERITY_COLORS.get(severity, "#36a64f")
        payload = {
            "attachments": [
                {
                    "color": color,
                    "title": title,
                    "text": message,
                    "footer": "bioAF Platform",
                    "fields": [
                        {
                            "title": "Severity",
                            "value": severity.capitalize(),
                            "short": True,
                        }
                    ],
                }
            ]
        }

        for attempt in range(MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(webhook_url, json=payload)
                    response.raise_for_status()
                logger.info("Slack webhook notification sent: %s", title)
                return True
            except Exception as e:
                backoff = BACKOFF_BASE ** (attempt * 2 + 1)
                logger.warning(
                    "Slack webhook attempt %d/%d failed: %s (backoff %ds)",
                    attempt + 1,
                    MAX_RETRIES,
                    e,
                    backoff,
                )
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(backoff)

        logger.error("Slack webhook delivery failed after %d attempts", MAX_RETRIES)
        return False
