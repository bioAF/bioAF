import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.component import VerificationCode
from app.models.organization import Organization
from app.schemas.bootstrap import (
    BootstrapStatus,
    ConfigureOrgRequest,
    ConfigureSmtpRequest,
    CreateAdminRequest,
    SmtpSettingsResponse,
    TestSmtpRequest,
    TestSmtpResponse,
)
from app.services.audit_service import log_action
from app.services.auth_service import AuthService
from app.services.component_service import ComponentService
from app.services.email_service import EmailService
from app.services import role_service
from app.services.user_service import UserService

logger = logging.getLogger("bioaf.bootstrap.api")

router = APIRouter(prefix="/api/bootstrap", tags=["bootstrap"])


async def _get_org(session: AsyncSession) -> Organization | None:
    result = await session.execute(select(Organization).limit(1))
    return result.scalar_one_or_none()


@router.get("/status", response_model=BootstrapStatus)
async def get_bootstrap_status(session: AsyncSession = Depends(get_session)):
    org = await _get_org(session)
    return BootstrapStatus(
        setup_complete=org.setup_complete if org else False,
        smtp_configured=org.smtp_configured if org else False,
    )


@router.post("/create-admin")
async def create_admin(body: CreateAdminRequest, session: AsyncSession = Depends(get_session)):
    # Only callable once — if org exists, block
    org = await _get_org(session)
    if org:
        raise HTTPException(status_code=409, detail="Admin account already created")

    # Create organization
    org = Organization(name="My Organization", setup_complete=False, smtp_configured=False)
    session.add(org)
    await session.flush()

    # Seed built-in roles for this organization
    from app.services.bootstrap_roles import seed_builtin_roles

    role_map = await seed_builtin_roles(session, org.id)

    # Create admin user
    user = await UserService.create_user(
        session,
        email=body.email,
        password=body.password,
        role_id=role_map["admin"],
        organization_id=org.id,
        name=body.name,
        status="active",
    )

    # Generate verification code
    code, code_hash = AuthService.generate_verification_code()
    verification = VerificationCode(
        user_id=user.id,
        code_hash=code_hash,
        purpose="email_verification",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    session.add(verification)
    await session.flush()

    # Initialize component states
    await ComponentService.initialize_states(session)

    # Try to send verification email
    email_sent = EmailService.send_verification_code(user.email, code)

    await session.commit()

    # Issue JWT token
    token = AuthService.create_token(user.id, user.email, user.role_id, org.id, role_name="admin")

    response = {
        "message": "Admin account created",
        "access_token": token,
        "token_type": "bearer",
        "email_sent": email_sent,
    }
    if not email_sent:
        response["verification_code"] = code  # Fallback for when SMTP isn't configured

    return response


@router.post("/configure-org")
async def configure_org(body: ConfigureOrgRequest, request: Request, session: AsyncSession = Depends(get_session)):
    current_user = request.state.current_user
    if not await role_service.has_permission(session, int(current_user["role_id"]), "infrastructure", "configure"):
        raise HTTPException(status_code=403, detail="Admin only")

    org = await _get_org(session)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    old_name = org.name
    org.name = body.org_name
    await session.flush()

    await log_action(
        session,
        user_id=int(current_user["sub"]),
        entity_type="organization",
        entity_id=org.id,
        action="configure",
        details={"name": body.org_name},
        previous_value={"name": old_name},
    )
    await session.commit()

    return {"message": "Organization configured"}


@router.get("/smtp-settings", response_model=SmtpSettingsResponse)
async def get_smtp_settings(request: Request, session: AsyncSession = Depends(get_session)):
    current_user = request.state.current_user
    if not await role_service.has_permission(session, int(current_user["role_id"]), "infrastructure", "configure"):
        raise HTTPException(status_code=403, detail="Admin only")

    org = await _get_org(session)
    if not org:
        return SmtpSettingsResponse(
            host="",
            port=587,
            username="",
            password="",
            from_address="",
            encryption="starttls",
            configured=False,
        )

    # Mask the password for display
    masked_password = ""
    if org.smtp_password:
        masked_password = (
            org.smtp_password[:2] + "***" + org.smtp_password[-2:] if len(org.smtp_password) > 4 else "***"
        )

    return SmtpSettingsResponse(
        host=org.smtp_host,
        port=org.smtp_port,
        username=org.smtp_username,
        password=masked_password,
        from_address=org.smtp_from_address,
        encryption=org.smtp_encryption,
        configured=org.smtp_configured,
    )


@router.post("/configure-smtp")
async def configure_smtp(body: ConfigureSmtpRequest, request: Request, session: AsyncSession = Depends(get_session)):
    current_user = request.state.current_user
    if not await role_service.has_permission(session, int(current_user["role_id"]), "infrastructure", "configure"):
        raise HTTPException(status_code=403, detail="Admin only")

    org = await _get_org(session)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Persist to database
    org.smtp_host = body.host
    org.smtp_port = body.port
    org.smtp_username = body.username
    org.smtp_password = body.password
    org.smtp_from_address = body.from_address
    org.smtp_encryption = body.encryption
    org.smtp_configured = True
    await session.flush()

    # Also update in-memory settings for immediate use
    from app.config import settings

    settings.smtp_host = body.host
    settings.smtp_port = body.port
    settings.smtp_username = body.username
    settings.smtp_password = body.password
    settings.smtp_from_address = body.from_address
    settings.smtp_encryption = body.encryption
    settings.smtp_configured = True

    await log_action(
        session,
        user_id=int(current_user["sub"]),
        entity_type="organization",
        entity_id=org.id,
        action="configure_smtp",
        details={
            "host": body.host,
            "port": body.port,
            "from_address": body.from_address,
            "encryption": body.encryption,
        },
    )
    await session.commit()

    return {"message": "SMTP configured"}


@router.post("/test-smtp", response_model=TestSmtpResponse)
async def test_smtp(body: TestSmtpRequest, request: Request, session: AsyncSession = Depends(get_session)):
    current_user = request.state.current_user
    if not await role_service.has_permission(session, int(current_user["role_id"]), "infrastructure", "configure"):
        raise HTTPException(status_code=403, detail="Admin only")

    if not EmailService.is_configured():
        return TestSmtpResponse(status="failed", to=body.to, detail="SMTP not configured")

    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    from app.config import settings as smtp_settings

    subject = "bioAF - Test Email"
    body_html = """
    <div style="font-family: sans-serif; max-width: 600px;">
        <h2>bioAF Test Email</h2>
        <p>This is a test email from your bioAF platform.</p>
        <p>If you received this, your SMTP settings are working correctly.</p>
    </div>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_settings.smtp_from_address
    msg["To"] = body.to
    msg.attach(MIMEText(body_html, "html"))

    try:
        encryption = getattr(smtp_settings, "smtp_encryption", "starttls")
        if encryption == "ssl":
            with smtplib.SMTP_SSL(smtp_settings.smtp_host, smtp_settings.smtp_port, timeout=10) as server:
                server.login(smtp_settings.smtp_username, smtp_settings.smtp_password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(smtp_settings.smtp_host, smtp_settings.smtp_port, timeout=10) as server:
                if encryption == "starttls":
                    server.starttls()
                server.login(smtp_settings.smtp_username, smtp_settings.smtp_password)
                server.send_message(msg)
        return TestSmtpResponse(status="sent", to=body.to, detail=f"Test email sent to {body.to}")
    except smtplib.SMTPAuthenticationError as e:
        raw = e.smtp_error.decode() if isinstance(e.smtp_error, bytes) else str(e.smtp_error)
        return TestSmtpResponse(
            status="failed",
            to=body.to,
            detail=f"Login rejected by the email provider. Check your username and password. (Server said: {raw})",
        )
    except smtplib.SMTPRecipientsRefused as e:
        details = "; ".join(f"{addr}: {err[1].decode()}" for addr, err in e.recipients.items())
        return TestSmtpResponse(
            status="failed",
            to=body.to,
            detail=f"The email provider refused the recipient address. ({details})",
        )
    except smtplib.SMTPSenderRefused as e:
        raw = e.smtp_error.decode() if isinstance(e.smtp_error, bytes) else str(e.smtp_error)
        return TestSmtpResponse(
            status="failed",
            to=body.to,
            detail=f"The email provider rejected the From address '{e.sender}'. "
            f"You may need to verify this address with your provider. (Server said: {raw})",
        )
    except smtplib.SMTPResponseException as e:
        raw = e.smtp_error.decode() if isinstance(e.smtp_error, bytes) else str(e.smtp_error)
        return TestSmtpResponse(
            status="failed",
            to=body.to,
            detail=f"The email provider returned an error. (Code {e.smtp_code}: {raw})",
        )
    except smtplib.SMTPConnectError:
        return TestSmtpResponse(
            status="failed",
            to=body.to,
            detail=f"Could not connect to {smtp_settings.smtp_host}:{smtp_settings.smtp_port}. "
            "Check the host, port, and encryption settings.",
        )
    except (TimeoutError, OSError) as e:
        return TestSmtpResponse(
            status="failed",
            to=body.to,
            detail=f"Could not reach the email server at {smtp_settings.smtp_host}:{smtp_settings.smtp_port}. "
            f"Check your host and port settings. ({e})",
        )
    except Exception as e:
        logger.error("SMTP test unexpected error: %s", e, exc_info=True)
        return TestSmtpResponse(
            status="failed",
            to=body.to,
            detail="Unexpected error while testing email delivery",
        )


@router.post("/complete")
async def complete_setup(request: Request, session: AsyncSession = Depends(get_session)):
    current_user = request.state.current_user
    if not await role_service.has_permission(session, int(current_user["role_id"]), "infrastructure", "configure"):
        raise HTTPException(status_code=403, detail="Admin only")

    org = await _get_org(session)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    org.setup_complete = True
    await session.flush()

    await log_action(
        session,
        user_id=int(current_user["sub"]),
        entity_type="organization",
        entity_id=org.id,
        action="complete_setup",
        details={"setup_complete": True},
    )
    await session.commit()

    return {"message": "Setup complete"}
