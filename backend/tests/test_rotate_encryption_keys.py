"""Tests for the rotate_encryption_keys CLI module.

The CLI walks every encrypted column, decrypts each row via the keyring,
and re-encrypts via the primary writer. These tests cover the core
rotation contract: legacy ciphertext under an older key still round-trips
through the ORM after running the command with the new key prepended.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cli import rotate_encryption_keys as rotate_cli


KEY_A = "yQWeSjhut-D91YUcqvDUfQ62wQHNq1G3vUstCSJpk9U="
KEY_B = "RULBtMyNqzJbIBpDe1gwY2YCCYkBI0UqjJsdAP-41AU="


def _factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _reload_encryption_with(raw_keys: str):
    """Reload encryption_service against a patched settings.encryption_keys."""
    import importlib

    from app import config

    config.settings.encryption_keys = raw_keys
    from app.platform import encryption_service

    importlib.reload(encryption_service)
    # rotate_encryption_keys imports encryption_service by name, so reload
    # the CLI module too to pick up the new MultiFernet instance.
    importlib.reload(rotate_cli)
    return encryption_service


@pytest.mark.asyncio
async def test_rotation_rewrites_legacy_ciphertext_under_new_primary(db_engine):
    """Write under key A, prepend key B, run the rotation; ciphertext on disk
    is now under key B alone."""
    from app.models.organization import Organization

    # 1. Start with only KEY_A available. Write an org via the ORM, which
    # encrypts under KEY_A as the primary writer.
    _reload_encryption_with(KEY_A)
    async with _factory(db_engine)() as session:
        org = Organization(name="RotateMe", smtp_password="legacy-secret")
        session.add(org)
        await session.commit()
        org_id = org.id

    # Confirm: KEY_A alone can decrypt this row.
    async with _factory(db_engine)() as session:
        raw = (
            await session.execute(
                sa_text("SELECT smtp_password FROM organizations WHERE id = :id"),
                {"id": org_id},
            )
        ).scalar_one()
        assert Fernet(KEY_A.encode()).decrypt(raw.encode()) == b"legacy-secret"

    # 2. Prepend KEY_B as the new primary writer; KEY_A remains a reader.
    _reload_encryption_with(f"{KEY_B},{KEY_A}")

    # 3. Run the rotation body against the existing engine.
    # We can't drive the full _main() (it builds its own engine from
    # settings.database_url, which points at the seeded DB in production
    # but not the per-worker test schema). Drive the same rotation loop
    # against the test session instead.
    async with _factory(db_engine)() as session:
        for table, column in rotate_cli._ENCRYPTED_COLUMNS:
            await rotate_cli._rotate_table_column(session, table, column, dry_run=False)
        await session.commit()

    # 4. After rotation, KEY_B alone must decrypt the row, and KEY_A alone
    # must NOT decrypt it.
    async with _factory(db_engine)() as session:
        raw_after = (
            await session.execute(
                sa_text("SELECT smtp_password FROM organizations WHERE id = :id"),
                {"id": org_id},
            )
        ).scalar_one()
        assert Fernet(KEY_B.encode()).decrypt(raw_after.encode()) == b"legacy-secret"
        with pytest.raises(Exception):
            Fernet(KEY_A.encode()).decrypt(raw_after.encode())

        # ORM still surfaces the plaintext (primary writer is KEY_B).
        loaded = (await session.execute(select(Organization).where(Organization.id == org_id))).scalar_one()
        assert loaded.smtp_password == "legacy-secret"


@pytest.mark.asyncio
async def test_rotation_dry_run_does_not_modify_rows(db_engine):
    """--dry-run must verify decryptability without writing anything."""
    from app.models.organization import Organization

    _reload_encryption_with(KEY_A)
    async with _factory(db_engine)() as session:
        org = Organization(name="DryRunOrg", smtp_password="untouched")
        session.add(org)
        await session.commit()
        org_id = org.id

        # Capture the on-disk ciphertext to compare after dry-run.
        before = (
            await session.execute(
                sa_text("SELECT smtp_password FROM organizations WHERE id = :id"),
                {"id": org_id},
            )
        ).scalar_one()

    _reload_encryption_with(f"{KEY_B},{KEY_A}")

    async with _factory(db_engine)() as session:
        rewritten, skipped = await rotate_cli._rotate_table_column(
            session, "organizations", "smtp_password", dry_run=True
        )
        await session.commit()  # nothing to commit, but safe.
        assert rewritten >= 1
        assert skipped == 0

        after = (
            await session.execute(
                sa_text("SELECT smtp_password FROM organizations WHERE id = :id"),
                {"id": org_id},
            )
        ).scalar_one()
        assert after == before, "dry-run rewrote a row it shouldn't have"


@pytest.mark.asyncio
async def test_rotation_aborts_if_keyring_cannot_decrypt(db_engine):
    """A row that no key on the current keyring can decrypt must fail loudly,
    not silently corrupt data."""
    from app.models.organization import Organization

    _reload_encryption_with(KEY_A)
    async with _factory(db_engine)() as session:
        org = Organization(name="OrphanedOrg", smtp_password="from-an-older-key")
        session.add(org)
        await session.commit()

    # Switch the keyring to KEY_B only; the existing row is unreadable.
    _reload_encryption_with(KEY_B)

    async with _factory(db_engine)() as session:
        with pytest.raises(SystemExit) as excinfo:
            await rotate_cli._rotate_table_column(session, "organizations", "smtp_password", dry_run=False)
        assert excinfo.value.code == 2


@pytest.mark.asyncio
async def test_rotation_handles_platform_config_sensitive_keys(db_engine):
    """The sensitive platform_config row must be re-encrypted alongside columns."""
    from app.platform.platform_config_service import PlatformConfigService

    _reload_encryption_with(KEY_A)
    async with _factory(db_engine)() as session:
        await PlatformConfigService.set(session, "gcp_service_account_key", "sa-key-1")
        await session.commit()

    _reload_encryption_with(f"{KEY_B},{KEY_A}")

    async with _factory(db_engine)() as session:
        rewritten, _ = await rotate_cli._rotate_platform_config(session, dry_run=False)
        await session.commit()
        assert rewritten == 1

        raw = (
            await session.execute(sa_text("SELECT value FROM platform_config WHERE key='gcp_service_account_key'"))
        ).scalar_one()
        # Now decryptable only by KEY_B.
        assert Fernet(KEY_B.encode()).decrypt(raw.encode()) == b"sa-key-1"
