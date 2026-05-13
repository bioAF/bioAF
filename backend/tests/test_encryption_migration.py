"""Tests for migration 076 (encrypt sensitive columns).

The migration is "eager one-shot": on a real Postgres DB with existing
plaintext rows, run it once, assert that the on-disk bytes are now
ciphertext and that the ORM still surfaces the original plaintext.
A second run must leave the data untouched (idempotency).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services import encryption_service
from app.services.platform_config_service import (
    SENSITIVE_PLATFORM_CONFIG_KEYS,
    PlatformConfigService,
)


def _factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.mark.asyncio
async def test_migration_backfill_round_trip(db_engine):
    """Insert plaintext via raw SQL (simulating pre-encryption state),
    run the backfill, verify on-disk bytes are ciphertext and ORM reads
    return the original plaintext."""
    from app.models.organization import Organization

    async with _factory(db_engine)() as session:
        # Insert a plaintext row by bypassing the ORM TypeDecorator. The
        # column is now TEXT after the type swap, so a direct INSERT with
        # raw plaintext is the closest simulation of legacy state.
        # NOT NULL columns must be supplied explicitly when bypassing the ORM.
        await session.execute(
            sa_text(
                "INSERT INTO organizations (name, setup_complete, smtp_configured, "
                "smtp_host, smtp_port, smtp_username, smtp_password, smtp_from_address, "
                "smtp_encryption, slack_client_id, slack_client_secret, slack_signing_secret) "
                "VALUES (:n, false, false, '', 587, '', :sp, '', 'starttls', '', :sc, :ss)"
            ),
            {
                "n": "Plaintext Org",
                "sc": "plain-slack-secret",
                "ss": "plain-signing-secret",
                "sp": "plain-smtp-pass",
            },
        )
        await session.commit()

        # Sanity: raw row really is plaintext.
        before = (
            await session.execute(
                sa_text(
                    "SELECT slack_client_secret, slack_signing_secret, smtp_password "
                    "FROM organizations WHERE name = 'Plaintext Org'"
                )
            )
        ).one()
        for v in before:
            assert not v.startswith("gAAAA")

        # Run the backfill body. Replicates migration 076 against the open session.
        for column in ("slack_client_secret", "slack_signing_secret", "smtp_password"):
            rows = (
                await session.execute(sa_text(f"SELECT id, {column} FROM organizations WHERE {column} IS NOT NULL"))
            ).fetchall()
            for row_id, value in rows:
                if encryption_service.looks_like_ciphertext(value):
                    continue
                cipher = encryption_service.encrypt(value)
                await session.execute(
                    sa_text(f"UPDATE organizations SET {column} = :v WHERE id = :id"),
                    {"v": cipher, "id": row_id},
                )
        await session.commit()

        # On-disk is ciphertext now.
        after_raw = (
            await session.execute(
                sa_text(
                    "SELECT slack_client_secret, slack_signing_secret, smtp_password "
                    "FROM organizations WHERE name = 'Plaintext Org'"
                )
            )
        ).one()
        for v in after_raw:
            assert v.startswith("gAAAA")

        # ORM read decrypts back to the original plaintext.
        from sqlalchemy import select

        org = (await session.execute(select(Organization).where(Organization.name == "Plaintext Org"))).scalar_one()
        assert org.slack_client_secret == "plain-slack-secret"
        assert org.slack_signing_secret == "plain-signing-secret"
        assert org.smtp_password == "plain-smtp-pass"


@pytest.mark.asyncio
async def test_migration_idempotent(db_engine):
    """Running the backfill twice must not double-encrypt rows."""
    async with _factory(db_engine)() as session:
        await session.execute(
            sa_text(
                "INSERT INTO organizations (name, setup_complete, smtp_configured, "
                "smtp_host, smtp_port, smtp_username, smtp_password, smtp_from_address, "
                "smtp_encryption, slack_client_id, slack_client_secret, slack_signing_secret) "
                "VALUES ('Org', false, false, '', 587, '', 'pw', '', 'starttls', '', '', '')"
            )
        )
        await session.commit()

        # First pass
        rows = (
            await session.execute(
                sa_text("SELECT id, smtp_password FROM organizations WHERE smtp_password IS NOT NULL")
            )
        ).fetchall()
        for row_id, value in rows:
            if encryption_service.looks_like_ciphertext(value):
                continue
            await session.execute(
                sa_text("UPDATE organizations SET smtp_password = :v WHERE id = :id"),
                {"v": encryption_service.encrypt(value), "id": row_id},
            )
        await session.commit()

        first_pass = (
            await session.execute(sa_text("SELECT smtp_password FROM organizations WHERE name='Org'"))
        ).scalar_one()

        # Second pass should be a no-op for already-encrypted rows.
        rows = (
            await session.execute(
                sa_text("SELECT id, smtp_password FROM organizations WHERE smtp_password IS NOT NULL")
            )
        ).fetchall()
        for row_id, value in rows:
            if encryption_service.looks_like_ciphertext(value):
                continue
            await session.execute(
                sa_text("UPDATE organizations SET smtp_password = :v WHERE id = :id"),
                {"v": encryption_service.encrypt(value), "id": row_id},
            )
        await session.commit()

        second_pass = (
            await session.execute(sa_text("SELECT smtp_password FROM organizations WHERE name='Org'"))
        ).scalar_one()

        assert first_pass == second_pass
        assert encryption_service.decrypt(second_pass) == "pw"


@pytest.mark.asyncio
async def test_migration_platform_config_sensitive_keys(db_engine):
    """Sensitive platform_config rows must be encrypted; non-sensitive must not."""
    async with _factory(db_engine)() as session:
        await session.execute(
            sa_text(
                "INSERT INTO platform_config (key, value) VALUES "
                "('gcp_project_id', 'my-project'), "
                "('gcp_service_account_key', 'SA-KEY-XYZ')"
            )
        )
        await session.commit()

        # Backfill body
        rows = (
            await session.execute(
                sa_text(
                    "SELECT id, key, value FROM platform_config WHERE key = ANY(:keys) AND value IS NOT NULL"
                ).bindparams(keys=list(SENSITIVE_PLATFORM_CONFIG_KEYS))
            )
        ).fetchall()
        for row_id, _key, value in rows:
            if encryption_service.looks_like_ciphertext(value):
                continue
            await session.execute(
                sa_text("UPDATE platform_config SET value = :v WHERE id = :id"),
                {"v": encryption_service.encrypt(value), "id": row_id},
            )
        await session.commit()

        sensitive_raw = (
            await session.execute(sa_text("SELECT value FROM platform_config WHERE key='gcp_service_account_key'"))
        ).scalar_one()
        assert sensitive_raw.startswith("gAAAA")

        nonsensitive = (
            await session.execute(sa_text("SELECT value FROM platform_config WHERE key='gcp_project_id'"))
        ).scalar_one()
        assert nonsensitive == "my-project"

        assert await PlatformConfigService.get(session, "gcp_service_account_key") == "SA-KEY-XYZ"
        assert await PlatformConfigService.get(session, "gcp_project_id") == "my-project"
