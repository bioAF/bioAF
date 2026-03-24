"""Email notification channel adapter using SMTP."""

import asyncio
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import settings

logger = logging.getLogger("bioaf.notifications.email")

MAX_RETRIES = 3
BACKOFF_BASE = 2  # seconds: 2, 8, 32


class EmailChannel:
    @staticmethod
    def is_configured() -> bool:
        return bool(settings.smtp_host and settings.smtp_configured)

    @staticmethod
    async def deliver(
        to: str,
        title: str,
        message: str,
        severity: str,
    ) -> bool:
        if not EmailChannel.is_configured():
            logger.warning("SMTP not configured, email to %s not sent", to)
            return False

        severity_label = severity.upper()
        body_html = f"""
        <div style="font-family: sans-serif; max-width: 600px;">
            <h2 style="color: #1a1a1a;">bioAF Notification</h2>
            <p style="color: #666; font-size: 12px;">Severity: {severity_label}</p>
            <h3>{title}</h3>
            <p>{message}</p>
            <hr style="border: none; border-top: 1px solid #eee;">
            <p style="color: #999; font-size: 11px;">
                This notification was sent by your bioAF platform.
                You can manage your notification preferences in your profile settings.
            </p>
        </div>
        """

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[bioAF] {title}"
        msg["From"] = settings.smtp_from_address
        msg["To"] = to
        msg.attach(MIMEText(body_html, "html"))

        for attempt in range(MAX_RETRIES):
            try:
                await asyncio.to_thread(_send_smtp, msg)
                logger.info("Email notification sent to %s: %s", to, title)
                return True
            except Exception as e:
                backoff = BACKOFF_BASE ** (attempt * 2 + 1)
                logger.warning(
                    "Email attempt %d/%d failed for %s: %s (backoff %ds)",
                    attempt + 1,
                    MAX_RETRIES,
                    to,
                    e,
                    backoff,
                )
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(backoff)

        logger.error("Email delivery failed after %d attempts to %s", MAX_RETRIES, to)
        return False


def _send_smtp(msg: MIMEMultipart) -> None:
    encryption = getattr(settings, "smtp_encryption", "starttls")
    if encryption == "ssl":
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            if encryption == "starttls":
                server.starttls()
            server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(msg)
