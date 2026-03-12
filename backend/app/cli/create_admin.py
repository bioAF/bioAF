"""CLI script to create an admin user for bioAF.

Usage:
    python -m app.cli.create_admin \
        --email admin@example.com \
        --password 'SecurePass123!' \
        --org-name 'Acme Biotech' \
        --org-slug acme-biotech
"""

import argparse
import asyncio
import os
import re
import sys

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.organization import Organization
from app.models.user import User
from app.services.auth_service import AuthService


def _validate_email(email: str) -> None:
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    if not re.match(pattern, email):
        raise ValueError(f"Invalid email: {email}")


def _validate_slug(slug: str) -> None:
    pattern = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
    if not re.match(pattern, slug):
        raise ValueError(
            f"Invalid org slug: {slug}. Must be lowercase alphanumeric with hyphens, no spaces or special characters."
        )


async def create_admin_user(
    session: AsyncSession,
    email: str,
    password: str,
    org_name: str,
    org_slug: str,
) -> None:
    """Create an admin user and organization for bioAF.

    Idempotent: if a user with the given email already exists, this is a no-op.
    """
    _validate_email(email)
    _validate_slug(org_slug)

    # Check if user already exists (idempotent)
    result = await session.execute(select(User).where(User.email == email))
    existing_user = result.scalar_one_or_none()
    if existing_user is not None:
        return

    # Create organization
    org = Organization(name=org_name, setup_complete=True, smtp_configured=False)
    session.add(org)
    await session.flush()

    # Create admin user with bcrypt-hashed password
    password_hash = AuthService.hash_password(password)
    user = User(
        email=email,
        password_hash=password_hash,
        role="admin",
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.flush()

    # Set org_slug in platform_config
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES ('org_slug', :slug) "
            "ON CONFLICT (key) DO UPDATE SET value = :slug"
        ),
        {"slug": org_slug},
    )

    await session.commit()


async def _main(args: argparse.Namespace) -> None:
    """Async entry point for the CLI."""
    database_url = os.environ.get("BIOAF_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: Set BIOAF_DATABASE_URL or DATABASE_URL environment variable.")
        sys.exit(1)

    engine = create_async_engine(database_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        await create_admin_user(
            session,
            email=args.email,
            password=args.password,
            org_name=args.org_name,
            org_slug=args.org_slug,
        )

    await engine.dispose()
    print(f"Admin user {args.email} created successfully.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a bioAF admin user")
    parser.add_argument("--email", required=True, help="Admin email address")
    parser.add_argument("--password", required=True, help="Admin password")
    parser.add_argument("--org-name", required=True, help="Organization name")
    parser.add_argument("--org-slug", required=True, help="Organization slug (lowercase, hyphens)")
    args = parser.parse_args()

    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
