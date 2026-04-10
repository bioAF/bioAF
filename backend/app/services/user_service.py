from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.services.auth_service import AuthService
from app.services.audit_service import log_action


class UserService:
    @staticmethod
    async def get_by_email(session: AsyncSession, email: str) -> User | None:
        result = await session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_id(session: AsyncSession, user_id: int) -> User | None:
        result = await session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def list_users(session: AsyncSession, org_id: int) -> list[User]:
        result = await session.execute(
            select(User).where(User.organization_id == org_id).order_by(User.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def create_user(
        session: AsyncSession,
        email: str,
        password: str,
        role_id: int,
        organization_id: int,
        name: str | None = None,
        status: str = "active",
        actor_user_id: int | None = None,
    ) -> User:
        password_hash = AuthService.hash_password(password)
        user = User(
            email=email,
            password_hash=password_hash,
            role_id=role_id,
            organization_id=organization_id,
            name=name,
            status=status,
        )
        session.add(user)
        await session.flush()

        await log_action(
            session,
            user_id=actor_user_id,
            entity_type="user",
            entity_id=user.id,
            action="create",
            details={"email": email, "role_id": role_id, "status": status},
        )
        return user

    @staticmethod
    async def invite_user(
        session: AsyncSession,
        email: str,
        role_id: int,
        organization_id: int,
        actor_user_id: int,
        name: str | None = None,
    ) -> tuple[User, str]:
        """Invite a user. Returns (user, invite_token)."""
        placeholder_hash = AuthService.hash_password("placeholder-not-usable")
        user = User(
            email=email,
            password_hash=placeholder_hash,
            role_id=role_id,
            organization_id=organization_id,
            name=name,
            status="invited",
        )
        session.add(user)
        await session.flush()

        invite_token = AuthService.generate_invite_token(user.id, email)

        await log_action(
            session,
            user_id=actor_user_id,
            entity_type="user",
            entity_id=user.id,
            action="invite",
            details={"email": email, "role_id": role_id},
        )
        return user, invite_token

    @staticmethod
    async def update_role(
        session: AsyncSession,
        user: User,
        new_role_id: int,
        actor_user_id: int,
    ) -> User:
        from app.services import role_service

        old_role_id = user.role_id
        old_role = await role_service.get_role_by_id(session, old_role_id)
        new_role = await role_service.get_role_by_id(session, new_role_id)
        old_role_name = old_role.name if old_role else str(old_role_id)
        new_role_name = new_role.name if new_role else str(new_role_id)

        user.role_id = new_role_id
        await session.flush()

        await log_action(
            session,
            user_id=actor_user_id,
            entity_type="user",
            entity_id=user.id,
            action="role_change",
            details={
                "new_role_id": new_role_id,
                "new_role_name": new_role_name,
                "target_email": user.email,
                "description": f"Changed {user.email} role from {old_role_name} to {new_role_name}",
            },
            previous_value={
                "old_role_id": old_role_id,
                "old_role_name": old_role_name,
            },
        )
        return user

    @staticmethod
    async def deactivate(
        session: AsyncSession,
        user: User,
        actor_user_id: int,
    ) -> User:
        old_status = user.status
        user.status = "deactivated"
        await session.flush()

        await log_action(
            session,
            user_id=actor_user_id,
            entity_type="user",
            entity_id=user.id,
            action="deactivate",
            details={
                "status": "deactivated",
                "target_email": user.email,
                "description": f"Deactivated {user.email}",
            },
            previous_value={"status": old_status},
        )
        return user

    @staticmethod
    async def reactivate(
        session: AsyncSession,
        user: User,
        actor_user_id: int,
    ) -> User:
        old_status = user.status
        user.status = "active"
        await session.flush()

        await log_action(
            session,
            user_id=actor_user_id,
            entity_type="user",
            entity_id=user.id,
            action="reactivate",
            details={
                "status": "active",
                "target_email": user.email,
                "description": f"Reactivated {user.email}",
            },
            previous_value={"status": old_status},
        )
        return user

    @staticmethod
    async def delete_user(
        session: AsyncSession,
        user: User,
        actor_user_id: int,
    ) -> None:
        """Hard-delete a user. Caller must verify eligibility first."""
        email = user.email
        user_id = user.id

        await session.delete(user)
        await session.flush()

        await log_action(
            session,
            user_id=actor_user_id,
            entity_type="user",
            entity_id=user_id,
            action="delete",
            details={
                "target_email": email,
                "description": f"Deleted user {email}",
            },
        )
