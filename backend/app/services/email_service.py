import logging
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import settings

logger = logging.getLogger("bioaf.email")


class EmailService:
    @staticmethod
    def is_configured() -> bool:
        return bool(settings.smtp_host and settings.smtp_configured)

    @staticmethod
    def send_email(to: str, subject: str, body_html: str) -> bool:
        if not EmailService.is_configured():
            logger.warning("SMTP not configured — email to %s not sent", to)
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
    def send_password_reset(to: str, code: str) -> bool:
        body = f"""
        <h2>bioAF Password Reset</h2>
        <p>Your password reset code is: <strong>{code}</strong></p>
        <p>This code expires in 10 minutes.</p>
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
