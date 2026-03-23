from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.session_credential import SessionCredential
from app.models.user import User
from app.schemas.auth import AdminResetPasswordRequest
from app.schemas.user import AcceptInviteRequest, BulkInvite, UserInvite, UserListResponse, UserResponse, UserUpdate
from app.services.audit_service import log_action
from app.services.auth_service import AuthService
from app.services.email_service import EmailService
from app.services.user_service import UserService

router = APIRouter(prefix="/api/users", tags=["users"])


def _require_admin(request: Request) -> dict:
    current_user = request.state.current_user
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


async def _count_active_admins(session: AsyncSession, org_id: int) -> int:
    from sqlalchemy import func

    result = await session.execute(
        select(func.count())
        .select_from(User)
        .where(
            User.organization_id == org_id,
            User.role == "admin",
            User.status == "active",
        )
    )
    return result.scalar_one()


@router.get("", response_model=UserListResponse)
async def list_users(request: Request, session: AsyncSession = Depends(get_session)):
    current_user = _require_admin(request)
    org_id = int(current_user["org_id"])
    users = await UserService.list_users(session, org_id)

    # Batch-fetch which users have session credentials configured
    cred_result = await session.execute(
        select(SessionCredential.user_id).where(SessionCredential.organization_id == org_id)
    )
    configured_user_ids = {row[0] for row in cred_result.all()}

    user_responses = []
    for u in users:
        resp = UserResponse.model_validate(u)
        resp.session_credentials_configured = u.id in configured_user_ids
        user_responses.append(resp)

    return UserListResponse(users=user_responses, total=len(user_responses))


@router.post("", response_model=UserResponse)
async def invite_user(body: UserInvite, request: Request, session: AsyncSession = Depends(get_session)):
    current_user = _require_admin(request)
    org_id = int(current_user["org_id"])
    actor_id = int(current_user["sub"])

    # Check if user already exists
    existing = await UserService.get_by_email(session, body.email)
    if existing:
        raise HTTPException(status_code=409, detail="User with this email already exists")

    user, invite_token = await UserService.invite_user(
        session, email=body.email, role=body.role, organization_id=org_id, actor_user_id=actor_id, name=body.name
    )

    # Send invitation email
    invite_link = f"/api/users/accept-invite?token={invite_token}"
    from app.models.organization import Organization

    result = await session.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one()
    EmailService.send_invitation(body.email, invite_link, org.name)

    await session.commit()
    return UserResponse.model_validate(user)


@router.post("/bulk-invite")
async def bulk_invite(body: BulkInvite, request: Request, session: AsyncSession = Depends(get_session)):
    current_user = _require_admin(request)
    org_id = int(current_user["org_id"])
    actor_id = int(current_user["sub"])

    results = []
    for invite in body.invites:
        existing = await UserService.get_by_email(session, invite.email)
        if existing:
            results.append({"email": invite.email, "status": "already_exists"})
            continue

        user, invite_token = await UserService.invite_user(
            session,
            email=invite.email,
            role=invite.role,
            organization_id=org_id,
            actor_user_id=actor_id,
            name=invite.name,
        )
        results.append({"email": invite.email, "status": "invited", "user_id": user.id})

    await session.commit()
    return {"results": results, "total_invited": sum(1 for r in results if r["status"] == "invited")}


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(user_id: int, request: Request, session: AsyncSession = Depends(get_session)):
    _require_admin(request)
    user = await UserService.get_by_id(session, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return UserResponse.model_validate(user)


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(user_id: int, body: UserUpdate, request: Request, session: AsyncSession = Depends(get_session)):
    current_user = _require_admin(request)
    actor_id = int(current_user["sub"])

    user = await UserService.get_by_id(session, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if body.role and body.role != user.role:
        # Guard: cannot demote the last active admin
        if user.role == "admin" and body.role != "admin":
            admin_count = await _count_active_admins(session, user.organization_id)
            if admin_count <= 1:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot change role of the last active admin",
                )
        user = await UserService.update_role(session, user, body.role, actor_id)
    if body.name is not None and body.name != (user.name or ""):
        old_name = user.name
        user.name = body.name
        await session.flush()
        await log_action(
            session,
            user_id=actor_id,
            entity_type="user",
            entity_id=user.id,
            action="update",
            details={"field": "name", "new_value": body.name, "target_email": user.email},
            previous_value={"field": "name", "old_value": old_name},
        )

    await session.commit()
    await session.refresh(user)
    return UserResponse.model_validate(user)


@router.post("/{user_id}/deactivate", response_model=UserResponse)
async def deactivate_user(user_id: int, request: Request, session: AsyncSession = Depends(get_session)):
    current_user = _require_admin(request)
    actor_id = int(current_user["sub"])

    user = await UserService.get_by_id(session, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.id == actor_id:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")

    if user.role == "admin":
        admin_count = await _count_active_admins(session, user.organization_id)
        if admin_count <= 1:
            raise HTTPException(status_code=400, detail="Cannot deactivate the last active admin")

    user = await UserService.deactivate(session, user, actor_id)
    await session.commit()
    await session.refresh(user)
    return UserResponse.model_validate(user)


@router.post("/{user_id}/lock")
async def lock_user(user_id: int, request: Request, session: AsyncSession = Depends(get_session)):
    current_user = _require_admin(request)
    actor_id = int(current_user["sub"])

    user = await UserService.get_by_id(session, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.id == actor_id:
        raise HTTPException(status_code=400, detail="Cannot lock yourself")

    if user.role == "admin":
        admin_count = await _count_active_admins(session, user.organization_id)
        if admin_count <= 1:
            raise HTTPException(status_code=400, detail="Cannot lock the last active admin")

    old_status = user.status
    user.status = "locked"
    await session.flush()
    await log_action(
        session,
        user_id=actor_id,
        entity_type="user",
        entity_id=user.id,
        action="lock",
        details={
            "target_email": user.email,
            "description": f"Locked {user.email}",
        },
        previous_value={"status": old_status},
    )
    await session.commit()
    await session.refresh(user)
    return UserResponse.model_validate(user)


@router.post("/{user_id}/resend-invite")
async def resend_invite(user_id: int, request: Request, session: AsyncSession = Depends(get_session)):
    current_user = _require_admin(request)
    org_id = int(current_user["org_id"])
    actor_id = int(current_user["sub"])

    user = await UserService.get_by_id(session, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.status != "invited":
        raise HTTPException(status_code=400, detail="Can only resend invites to users with invited status")

    invite_token = AuthService.generate_invite_token(user.id, user.email)
    invite_link = f"/api/users/accept-invite?token={invite_token}"

    from app.models.organization import Organization

    result = await session.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one()
    EmailService.send_invitation(user.email, invite_link, org.name)

    await log_action(
        session,
        user_id=actor_id,
        entity_type="user",
        entity_id=user.id,
        action="resend_invite",
        details={"target_email": user.email, "description": f"Resent invitation email to {user.email}"},
    )
    await session.commit()

    return {"message": "Invitation resent"}


@router.post("/{user_id}/admin-reset-password")
async def admin_reset_password(
    user_id: int,
    body: AdminResetPasswordRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    current_user = _require_admin(request)
    actor_id = int(current_user["sub"])

    user = await UserService.get_by_id(session, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if body.mode == "email":
        from datetime import datetime, timedelta, timezone

        from app.models.component import VerificationCode

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

        await log_action(
            session,
            user_id=actor_id,
            entity_type="user",
            entity_id=user.id,
            action="admin_reset_password",
            details={
                "mode": "email",
                "target_email": user.email,
                "description": f"Sent password reset email to {user.email}",
            },
        )
        await session.commit()
        return {"message": "Password reset email sent"}

    elif body.mode == "temporary":
        if not body.temporary_password:
            raise HTTPException(status_code=400, detail="Temporary password is required")

        user.password_hash = AuthService.hash_password(body.temporary_password)
        await session.flush()

        await log_action(
            session,
            user_id=actor_id,
            entity_type="user",
            entity_id=user.id,
            action="admin_reset_password",
            details={
                "mode": "temporary",
                "target_email": user.email,
                "description": f"Set temporary password for {user.email}",
            },
        )
        await session.commit()
        return {"message": "Temporary password set"}

    else:
        raise HTTPException(status_code=400, detail="Mode must be 'email' or 'temporary'")


@router.post("/accept-invite")
async def accept_invite(body: AcceptInviteRequest, session: AsyncSession = Depends(get_session)):
    try:
        payload = AuthService.validate_invite_token(body.token)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid or expired invitation")

    user = await UserService.get_by_id(session, int(payload["sub"]))
    if not user or user.status != "invited":
        raise HTTPException(status_code=400, detail="Invalid invitation")

    user.password_hash = AuthService.hash_password(body.password)
    user.status = "active"
    if body.name:
        user.name = body.name
    await session.flush()

    from app.services.audit_service import log_action

    await log_action(
        session,
        user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        action="accept_invite",
        details={"email": user.email},
    )
    await session.commit()

    token = AuthService.create_token(user.id, user.email, user.role, user.organization_id)
    return {"access_token": token, "token_type": "bearer"}
