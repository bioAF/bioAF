"""Service for generating and verifying terminal-issued setup codes."""

import os
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.organization import Organization
from app.models.user import User

# Unambiguous charset: no 0/O, 1/l/I
SETUP_CODE_CHARSET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
SETUP_CODE_LENGTH = 6
SETUP_CODE_TTL = timedelta(hours=1)


class SetupCodeService:
    @staticmethod
    async def generate_code(session: AsyncSession, org: Organization) -> str:
        """Generate a setup code, store its bcrypt hash and expiry on the org.

        Returns the plaintext code (shown in the terminal).
        """
        code = "".join(secrets.choice(SETUP_CODE_CHARSET) for _ in range(SETUP_CODE_LENGTH))
        code_hash = bcrypt.hashpw(code.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

        org.setup_code_hash = code_hash
        org.setup_code_expires_at = datetime.now(timezone.utc) + SETUP_CODE_TTL
        session.add(org)
        await session.flush()

        return code

    @staticmethod
    async def verify_code(session: AsyncSession, org: Organization, code: str) -> bool:
        """Verify a setup code against the stored hash.

        Returns True if valid and not expired. On success, nulls the hash
        so the code is single-use.
        """
        if org.setup_code_hash is None:
            return False

        if org.setup_code_expires_at is None or org.setup_code_expires_at < datetime.now(timezone.utc):
            return False

        if not bcrypt.checkpw(code.encode("utf-8"), org.setup_code_hash.encode("utf-8")):
            return False

        # Single-use: null the hash and expiry
        org.setup_code_hash = None
        org.setup_code_expires_at = None
        session.add(org)
        await session.flush()

        return True


async def generate_setup_code_for_cli() -> dict:
    """CLI bridge: open a DB session, generate a setup code, return JSON-ready dict.

    Called from the bash CLI via ``docker exec -T backend python -c "..."``.
    """
    database_url = os.environ.get("BIOAF_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not database_url:
        return {"error": "DATABASE_URL not set"}

    engine = create_async_engine(database_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with factory() as session:
            # Check for existing admin
            from app.models.role import Role

            admin_exists = (
                await session.execute(
                    select(User.id).join(Role, User.role_id == Role.id).where(Role.name == "admin").limit(1)
                )
            ).scalar_one_or_none() is not None

            if admin_exists:
                return {"already_setup": True}

            # Get or create org
            org = (await session.execute(select(Organization).limit(1))).scalar_one_or_none()
            if not org:
                org = Organization(name="My Organization", setup_complete=False, smtp_configured=False)
                session.add(org)
                await session.flush()

            code = await SetupCodeService.generate_code(session, org)
            expires_at = org.setup_code_expires_at.isoformat() if org.setup_code_expires_at else None
            await session.commit()

            return {"already_setup": False, "code": code, "expires_at": expires_at}
    finally:
        await engine.dispose()
