import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.session_credential import SessionCredential
from app.services.auth_service import AuthService
from app.services.audit_service import log_action


class SessionCredentialService:
    @staticmethod
    def generate_username(email: str) -> str:
        """Generate a Unix username from an email address.

        Strips the domain, removes dots and non-alphanumeric chars, lowercases.
        """
        local_part = email.split("@")[0].lower()
        username = re.sub(r"[^a-z0-9]", "", local_part)
        return username or "user"

    @staticmethod
    def validate_username(username: str) -> str | None:
        """Validate a custom username. Returns error message or None."""
        if len(username) < 3:
            return "Username must be at least 3 characters"
        if len(username) > 32:
            return "Username must be at most 32 characters"
        if not re.match(r"^[a-z][a-z0-9_]*$", username):
            return "Username must start with a letter and contain only lowercase letters, numbers, and underscores"
        return None

    @staticmethod
    async def get_by_user_id(session: AsyncSession, user_id: int) -> SessionCredential | None:
        result = await session.execute(select(SessionCredential).where(SessionCredential.user_id == user_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def _resolve_unique_username(
        session: AsyncSession,
        base_username: str,
        org_id: int,
        exclude_user_id: int | None = None,
    ) -> str:
        """Find a unique username within the org, appending numeric suffix if needed."""
        candidate = base_username
        suffix = 2
        while True:
            query = select(SessionCredential).where(
                SessionCredential.organization_id == org_id,
                SessionCredential.username == candidate,
            )
            if exclude_user_id is not None:
                query = query.where(SessionCredential.user_id != exclude_user_id)
            result = await session.execute(query)
            if result.scalar_one_or_none() is None:
                return candidate
            candidate = f"{base_username}{suffix}"
            suffix += 1

    @staticmethod
    async def create_or_update(
        session: AsyncSession,
        user_id: int,
        org_id: int,
        email: str,
        password: str,
        username: str | None = None,
        actor_user_id: int | None = None,
    ) -> SessionCredential:
        """Create or update session credentials for a user."""
        existing = await SessionCredentialService.get_by_user_id(session, user_id)

        if username is None:
            base = SessionCredentialService.generate_username(email)
            if existing and existing.username:
                # Keep existing username if not explicitly changing
                resolved_username = existing.username
            else:
                resolved_username = await SessionCredentialService._resolve_unique_username(
                    session,
                    base,
                    org_id,
                    exclude_user_id=user_id,
                )
        else:
            resolved_username = await SessionCredentialService._resolve_unique_username(
                session,
                username,
                org_id,
                exclude_user_id=user_id,
            )

        password_hash = AuthService.hash_password(password)

        if existing:
            existing.username = resolved_username
            existing.password_hash = password_hash
            await session.flush()
            action = "update"
            cred = existing
        else:
            cred = SessionCredential(
                user_id=user_id,
                organization_id=org_id,
                username=resolved_username,
                password_hash=password_hash,
            )
            session.add(cred)
            await session.flush()
            action = "create"

        await log_action(
            session,
            user_id=actor_user_id or user_id,
            entity_type="session_credential",
            entity_id=cred.id,
            action=action,
            details={"username": resolved_username},
        )
        return cred
