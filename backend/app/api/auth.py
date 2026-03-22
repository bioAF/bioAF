from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.component import VerificationCode
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    UserProfile,
    VerifyEmailRequest,
)
from app.services.access_log_service import AccessLogService
from app.services.audit_service import log_action
from app.services.auth_service import AuthService
from app.services.email_service import EmailService
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

    token = AuthService.create_token(user.id, user.email, user.role, user.organization_id)

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

    token = AuthService.create_token(user.id, user.email, user.role, user.organization_id)
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
    return user
