import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session
from app.models.organization import Organization
from app.models.user import User
from app.schemas.bootstrap import (
    BootstrapStatus,
    ConfigureOrgRequest,
    ConfigureSmtpRequest,
    CreateAdminRequest,
    GenerateSetupCodeResponse,
    SmtpSettingsResponse,
    TestSmtpRequest,
    TestSmtpResponse,
    VerifySetupCodeRequest,
    VerifySetupCodeResponse,
)
from app.services.audit_service import log_action
from app.services.auth_service import AuthService
from app.services.component_service import ComponentService
from app.services.email_service import EmailService
from app.services import role_service
from app.services.setup_code_service import SetupCodeService
from app.services.user_service import UserService

logger = logging.getLogger("bioaf.bootstrap.api")

router = APIRouter(prefix="/api/bootstrap", tags=["bootstrap"])


async def _get_org(session: AsyncSession) -> Organization | None:
    result = await session.execute(select(Organization).limit(1))
    return result.scalar_one_or_none()


async def _has_admin(session: AsyncSession) -> bool:
    """Check whether any admin user exists."""
    from app.models.role import Role

    result = await session.execute(
        select(User.id).join(Role, User.role_id == Role.id).where(Role.name == "admin").limit(1)
    )
    return result.scalar_one_or_none() is not None


def _validate_setup_token(request: Request) -> dict:
    """Extract and validate a setup JWT from the Authorization header."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Setup token required")
    token = auth_header.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        if payload.get("purpose") != "setup":
            raise HTTPException(status_code=401, detail="Not a setup token")
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired setup token")


@router.get("/status", response_model=BootstrapStatus)
async def get_bootstrap_status(session: AsyncSession = Depends(get_session)):
    org = await _get_org(session)
    has_admin_user = await _has_admin(session) if org else False
    has_code = bool(org and org.setup_code_hash is not None) if org else False
    return BootstrapStatus(
        setup_complete=org.setup_complete if org else False,
        smtp_configured=org.smtp_configured if org else False,
        has_setup_code=has_code,
        has_admin=has_admin_user,
    )


@router.post("/generate-setup-code", response_model=GenerateSetupCodeResponse)
async def generate_setup_code(request: Request, session: AsyncSession = Depends(get_session)):
    """Generate a setup code for terminal-based setup. No auth required."""
    # Defense in depth: log if not from localhost/docker
    client_host = request.client.host if request.client else "unknown"
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        logger.warning("generate-setup-code called from non-local address: %s", client_host)

    # If admin already exists, return already_setup
    if await _has_admin(session):
        return GenerateSetupCodeResponse(already_setup=True)

    # Get or create org
    org = await _get_org(session)
    if not org:
        org = Organization(name="My Organization", setup_complete=False, smtp_configured=False)
        session.add(org)
        await session.flush()

    code = await SetupCodeService.generate_code(session, org)
    await session.commit()

    return GenerateSetupCodeResponse(
        code=code,
        expires_at=org.setup_code_expires_at.isoformat() if org.setup_code_expires_at else None,
        already_setup=False,
    )


@router.post("/verify-setup-code", response_model=VerifySetupCodeResponse)
async def verify_setup_code(body: VerifySetupCodeRequest, session: AsyncSession = Depends(get_session)):
    """Verify a setup code and return a setup session JWT."""
    org = await _get_org(session)
    if not org:
        raise HTTPException(status_code=401, detail="Invalid setup code")

    valid = await SetupCodeService.verify_code(session, org, body.code)
    if not valid:
        raise HTTPException(status_code=401, detail="Invalid or expired setup code")

    await session.commit()

    # Issue a short-lived setup JWT
    payload = {
        "purpose": "setup",
        "org_id": org.id,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=15),
    }
    setup_token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

    return VerifySetupCodeResponse(setup_token=setup_token, message="Setup code verified")


@router.post("/create-admin")
async def create_admin(body: CreateAdminRequest, request: Request, session: AsyncSession = Depends(get_session)):
    # Require setup token
    _validate_setup_token(request)

    # Only callable once -- if admin exists, block
    if await _has_admin(session):
        raise HTTPException(status_code=409, detail="Admin account already created")

    # Get or create organization
    org = await _get_org(session)
    if not org:
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

    # Initialize component states
    await ComponentService.initialize_states(session)

    await session.commit()

    # Issue JWT token
    token = AuthService.create_token(user.id, user.email, user.role_id, org.id, role_name="admin")

    return {
        "message": "Admin account created",
        "access_token": token,
        "token_type": "bearer",
    }


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
