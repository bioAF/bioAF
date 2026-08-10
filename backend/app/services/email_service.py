import logging
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.organization import Organization

logger = logging.getLogger("bioaf.email")


def apply_smtp_to_settings(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    from_address: str,
    encryption: str,
    configured: bool = True,
) -> None:
    """Write SMTP config into the in-memory settings singleton.

    Single source of truth for which fields make up SMTP config, shared by the
    save path (configure_smtp) and the startup load path so the two cannot
    drift.
    """
    settings.smtp_host = host
    settings.smtp_port = port
    settings.smtp_username = username
    settings.smtp_password = password
    settings.smtp_from_address = from_address
    settings.smtp_encryption = encryption
    settings.smtp_configured = configured


async def load_persisted_smtp_settings(session: AsyncSession) -> bool:
    """Hydrate the in-memory settings from the org's persisted SMTP config.

    Read the Organization row through the ORM so the EncryptedString
    smtp_password column is decrypted. A raw SQL read returns the Fernet
    ciphertext, which would then be sent to the SMTP server as the login
    password and fail authentication.

    Returns True if configured settings were applied, False otherwise.
    """
    org = (await session.execute(select(Organization).limit(1))).scalar_one_or_none()
    if not (org and org.smtp_configured and org.smtp_host):
        return False
    apply_smtp_to_settings(
        host=org.smtp_host,
        port=org.smtp_port,
        username=org.smtp_username,
        password=org.smtp_password,
        from_address=org.smtp_from_address,
        encryption=org.smtp_encryption,
    )
    return True


class EmailService:
    @staticmethod
    def is_configured() -> bool:
        return bool(settings.smtp_host and settings.smtp_configured)

    @staticmethod
    def send_email(to: str, subject: str, body_html: str) -> bool:
        if not EmailService.is_configured():
            logger.warning("SMTP not configured: email to %s not sent", to)
            return False

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.smtp_from_address
        msg["To"] = to
        msg.attach(MIMEText(body_html, "html"))

        for attempt in range(2):
            try:
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
                logger.info("Email sent to %s: %s", to, subject)
                return True
            except Exception as e:
                logger.warning("Email send attempt %d failed: %s", attempt + 1, e)
                if attempt == 0:
                    time.sleep(5)
        return False

    @staticmethod
    def send_verification_code(to: str, code: str) -> bool:
        body = f"""
        <h2>bioAF Email Verification</h2>
        <p>Your verification code is: <strong>{code}</strong></p>
        <p>This code expires in 10 minutes.</p>
        """
        return EmailService.send_email(to, "bioAF - Email Verification", body)

    @staticmethod
    def send_password_reset(to: str, code: str, reset_link: str) -> bool:
        body = f"""
        <h2>bioAF Password Reset</h2>
        <p>We received a request to reset your bioAF password.</p>
        <p><a href="{reset_link}">Reset your password</a></p>
        <p>When prompted, enter this reset code:</p>
        <p style="font-size:20px"><strong>{code}</strong></p>
        <p>This link and code expire in 60 minutes. If you did not request a reset,
        you can ignore this email.</p>
        """
        return EmailService.send_email(to, "bioAF - Password Reset", body)

    @staticmethod
    def send_invitation(to: str, invite_link: str, org_name: str) -> bool:
        body = f"""
        <h2>You've been invited to bioAF</h2>
        <p>You've been invited to join <strong>{org_name}</strong> on bioAF.</p>
        <p><a href="{invite_link}">Accept Invitation</a></p>
        <p>This link expires in 7 days.</p>
        """
        return EmailService.send_email(to, f"bioAF - Invitation to {org_name}", body)
