"""Slack notification channel adapter supporting bot token (chat.postMessage) and legacy webhooks."""

import logging

import httpx

from app.services import http_policy, request_pacing

logger = logging.getLogger("bioaf.notifications.slack")

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


def _safe_webhook_failure(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


# Slack's documented transient chat.postMessage errors; every other ok:false
# error (auth, channel, archived, payload) is permanent and fails at once.
TRANSIENT_ERRORS = frozenset({"ratelimited", "internal_error", "fatal_error", "service_unavailable", "request_timeout"})


def _slack_error(response: httpx.Response) -> str | None:
    """The ok:false error code, or None for ok:true or an unreadable body."""
    try:
        data = response.json()
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("ok"):
        return None
    error = data.get("error")
    return error if isinstance(error, str) and error else "unknown_error"


async def _post(client: httpx.AsyncClient, url: str, label: str, **kwargs) -> httpx.Response:
    return await http_policy.send(
        client,
        "POST",
        url,
        policy=http_policy.SLACK_DELIVERY,
        label=label,
        clock=request_pacing.registry().clock,
        retry_response=lambda r: _slack_error(r) in TRANSIENT_ERRORS,
        **kwargs,
    )


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

        Returns (success, error_code). error_code is empty on success. Transient
        failures are retried under SLACK_DELIVERY (at-least-once); permanent
        Slack errors return at once.
        """
        payload = {
            "channel": channel_id,
            "text": f"{title}: {message}",
            "attachments": _build_blocks(title, message, severity),
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await _post(
                    client,
                    SLACK_POST_MESSAGE_URL,
                    "slack.post_message",
                    json=payload,
                    headers={"Authorization": f"Bearer {bot_token}"},
                )
                try:
                    data = response.json()
                except ValueError:
                    data = None
        except (httpx.HTTPError, TimeoutError) as e:
            logger.error("Slack delivery failed: %s", type(e).__name__)
            return False, type(e).__name__
        if isinstance(data, dict) and data.get("ok"):
            logger.info("Slack notification sent to %s: %s", channel_id, title)
            return True, ""
        error = data.get("error") if isinstance(data, dict) else None
        error = error if isinstance(error, str) and error else f"http_{response.status_code}"
        logger.warning("Slack API error: %s (status %s)", error, response.status_code)
        return False, error

    @staticmethod
    async def deliver_webhook(
        webhook_url: str,
        title: str,
        message: str,
        severity: str,
    ) -> bool:
        """Legacy webhook delivery for backward compatibility.

        The webhook URL is the credential, and httpx errors embed it; logs carry
        only the status or exception class."""
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
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await _post(client, webhook_url, "slack.webhook", json=payload)
                response.raise_for_status()
        except (httpx.HTTPError, TimeoutError) as e:
            logger.error("Slack webhook delivery failed: %s", _safe_webhook_failure(e))
            return False
        logger.info("Slack webhook notification sent: %s", title)
        return True
