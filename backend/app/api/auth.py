from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.component import VerificationCode
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    UserProfile,
    VerifyEmailRequest,
)
from app.schemas.session_credential import SessionCredentialRequest, SessionCredentialResponse
from app.services.access_log_service import AccessLogService
from app.services.audit_service import log_action
from app.services import role_service
from app.services.auth_service import AuthService
from app.services.email_service import EmailService
from app.services.session_credential_service import SessionCredentialService
from app.services.user_service import UserService

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, session: AsyncSession = Depends(get_session)):
    user = await UserService.get_by_email(session, body.email)
    if not user or not AuthService.verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if user.status == "deactivated":
        raise HTTPException(status_code=403, detail="Account deactivated")

    if user.status == "invited":
        raise HTTPException(status_code=403, detail="Please accept your invitation first")

    user.last_login = datetime.now(timezone.utc)
    role = await role_service.get_role_by_id(session, user.role_id)
    role_name = role.name if role else ""
    token = AuthService.create_token(user.id, user.email, user.role_id, user.organization_id, role_name=role_name)

    await log_action(session, user_id=user.id, entity_type="auth", entity_id=user.id, action="login")
    await AccessLogService.log_access(
        session,
        user.organization_id,
        user.id,
        "auth",
        str(user.id),
        "login",
        {"email": user.email},
    )
    await session.commit()

    return LoginResponse(access_token=token)


@router.post("/refresh", response_model=LoginResponse)
async def refresh_token(request: Request, session: AsyncSession = Depends(get_session)):
    current_user = request.state.current_user
    user_id = int(current_user["sub"])
    user = await UserService.get_by_id(session, user_id)
    if not user or user.status != "active":
        raise HTTPException(status_code=401, detail="User not found or inactive")

    role = await role_service.get_role_by_id(session, user.role_id)
    role_name = role.name if role else ""
    token = AuthService.create_token(user.id, user.email, user.role_id, user.organization_id, role_name=role_name)
    return LoginResponse(access_token=token)


@router.post("/verify-email")
async def verify_email(body: VerifyEmailRequest, session: AsyncSession = Depends(get_session)):
    user = await UserService.get_by_email(session, body.email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    from sqlalchemy import select

    result = await session.execute(
        select(VerificationCode)
        .where(VerificationCode.user_id == user.id)
        .where(VerificationCode.purpose == "email_verification")
        .where(VerificationCode.used == False)  # noqa: E712
        .where(VerificationCode.expires_at > datetime.now(timezone.utc))
        .order_by(VerificationCode.created_at.desc())
        .limit(1)
    )
    code_record = result.scalar_one_or_none()

    if not code_record or not AuthService.verify_code(body.code, code_record.code_hash):
        raise HTTPException(status_code=400, detail="Invalid or expired verification code")

    code_record.used = True
    await session.flush()

    await log_action(session, user_id=user.id, entity_type="auth", entity_id=user.id, action="verify_email")
    await session.commit()

    return {"message": "Email verified successfully"}


@router.post("/request-reset")
async def request_reset(body: PasswordResetRequest, session: AsyncSession = Depends(get_session)):
    user = await UserService.get_by_email(session, body.email)
    if not user:
        # Don't reveal whether email exists
        return {"message": "If the email exists, a reset code has been sent"}

    code, code_hash = AuthService.generate_verification_code()
    verification = VerificationCode(
        user_id=user.id,
        code_hash=code_hash,
        purpose="password_reset",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    session.add(verification)
    await session.flush()

    EmailService.send_password_reset(user.email, code)

    await log_action(session, user_id=user.id, entity_type="auth", entity_id=user.id, action="request_reset")
    await session.commit()

    return {"message": "If the email exists, a reset code has been sent"}


@router.post("/reset-password")
async def reset_password(body: PasswordResetConfirm, session: AsyncSession = Depends(get_session)):
    user = await UserService.get_by_email(session, body.email)
    if not user:
        raise HTTPException(status_code=400, detail="Invalid reset request")

    from sqlalchemy import select

    result = await session.execute(
        select(VerificationCode)
        .where(VerificationCode.user_id == user.id)
        .where(VerificationCode.purpose == "password_reset")
        .where(VerificationCode.used == False)  # noqa: E712
        .where(VerificationCode.expires_at > datetime.now(timezone.utc))
        .order_by(VerificationCode.created_at.desc())
        .limit(1)
    )
    code_record = result.scalar_one_or_none()

    if not code_record or not AuthService.verify_code(body.code, code_record.code_hash):
        raise HTTPException(status_code=400, detail="Invalid or expired reset code")

    code_record.used = True
    user.password_hash = AuthService.hash_password(body.new_password)
    await session.flush()

    await log_action(session, user_id=user.id, entity_type="auth", entity_id=user.id, action="reset_password")
    await session.commit()

    return {"message": "Password reset successfully"}


@router.get("/me", response_model=UserProfile)
async def get_current_user(request: Request, session: AsyncSession = Depends(get_session)):
    current_user = request.state.current_user
    user_id = int(current_user["sub"])
    user = await UserService.get_by_id(session, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    profile = UserProfile.model_validate(user)
    role = await role_service.get_role_by_id(session, user.role_id)
    profile.role_name = role.name if role else ""
    from app.schemas.auth import PermissionEntry

    perms = await role_service.get_role_permissions(session, user.role_id)
    profile.permissions = [PermissionEntry(**p) for p in perms]
    return profile


@router.post("/me/change-password")
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    user_id = int(current_user["sub"])
    user = await UserService.get_by_id(session, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if not AuthService.verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    user.password_hash = AuthService.hash_password(body.new_password)
    await session.flush()

    await log_action(
        session,
        user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        action="change_password",
        details={"description": f"{user.email} changed their own password"},
    )
    await session.commit()

    return {"message": "Password changed successfully"}


@router.get("/me/session-credentials", response_model=SessionCredentialResponse)
async def get_session_credentials(request: Request, session: AsyncSession = Depends(get_session)):
    current_user = request.state.current_user
    user_id = int(current_user["sub"])
    cred = await SessionCredentialService.get_by_user_id(session, user_id)
    if not cred:
        return SessionCredentialResponse(configured=False)
    return SessionCredentialResponse(
        configured=True,
        username=cred.username,
        created_at=cred.created_at,
        updated_at=cred.updated_at,
    )


@router.put("/me/session-credentials", response_model=SessionCredentialResponse)
async def upsert_session_credentials(
    body: SessionCredentialRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    user_id = int(current_user["sub"])
    org_id = int(current_user["org_id"])
    email = str(current_user["email"])

    cred = await SessionCredentialService.create_or_update(
        session,
        user_id=user_id,
        org_id=org_id,
        email=email,
        password=body.password,
        username=body.username,
    )
    await session.commit()
    await session.refresh(cred)
    return SessionCredentialResponse(
        configured=True,
        username=cred.username,
        created_at=cred.created_at,
        updated_at=cred.updated_at,
    )


@router.get("/me/ssh-key")
async def get_ssh_key(request: Request, session: AsyncSession = Depends(get_session)):
    """Return the user's SSH public key, or indicate none exists."""
    current_user = request.state.current_user
    user_id = int(current_user["sub"])
    cred = await SessionCredentialService.get_by_user_id(session, user_id)
    if not cred or not cred.ssh_public_key:
        return {"configured": False, "public_key": None}
    return {"configured": True, "public_key": cred.ssh_public_key}


@router.post("/me/ssh-key/generate")
async def generate_ssh_key(request: Request, session: AsyncSession = Depends(get_session)):
    """Generate an RSA key pair for the user. Stores the private key, returns the public key."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    current_user = request.state.current_user
    user_id = int(current_user["sub"])
    email = str(current_user["email"])

    cred = await SessionCredentialService.get_by_user_id(session, user_id)
    if not cred:
        raise HTTPException(400, "Set up session credentials first before generating an SSH key")

    # Generate RSA 4096-bit key pair
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    public_key = private_key.public_key()
    public_openssh = public_key.public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode("utf-8")

    # Add email as comment
    public_openssh = f"{public_openssh} {email}"

    cred.ssh_public_key = public_openssh
    cred.ssh_private_key = private_pem
    await session.flush()
    await session.commit()

    await log_action(
        session,
        user_id=user_id,
        entity_type="session_credential",
        entity_id=cred.id,
        action="generate_ssh_key",
        details={},
    )

    return {
        "public_key": public_openssh,
        "message": "SSH key generated. Add the public key to your GitHub account under Settings > SSH and GPG keys.",
    }
