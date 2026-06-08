"""CLI: re-encrypt every sensitive column under the current primary key.

Background: BIOAF_ENCRYPTION_KEYS is a comma-separated MultiFernet keyring.
The first key is the writer; the rest are accepted readers. After
prepending a new key (step 2 of the rotation runbook), existing rows are
still encrypted under the old key -- they decrypt fine, but new writes
go through the new key. This script walks every encrypted column,
decrypts each row via the keyring, and re-encrypts it via the primary
writer, so the old key can be safely removed in step 5.

Idempotent: rows that already encrypt-decrypt cleanly under the current
primary writer are still rewritten (a no-op functionally; the ciphertext
changes only because Fernet tokens include an IV, not because the key
changed). This is fine for hundreds-of-rows scale; abort early if any
row fails to decrypt (likely indicates a missing key on the keyring).

Usage:
    python -m app.cli.rotate_encryption_keys
    python -m app.cli.rotate_encryption_keys --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings, validate_encryption_keys
from app.platform import encryption_service
from app.platform.platform_config_service import SENSITIVE_PLATFORM_CONFIG_KEYS

# Same shape as migration 076's table list. Kept in sync manually because the
# migration is frozen-in-time; this command is the moving target.
_ENCRYPTED_COLUMNS: list[tuple[str, str]] = [
    ("organizations", "smtp_password"),
    ("organizations", "slack_client_secret"),
    ("organizations", "slack_signing_secret"),
    ("session_credentials", "ssh_private_key"),
    ("compute_sessions", "heartbeat_token"),
    ("slack_installations", "bot_token"),
    ("slack_webhooks", "webhook_url"),
]


async def _rotate_table_column(session: AsyncSession, table: str, column: str, dry_run: bool) -> tuple[int, int]:
    """Re-encrypt every non-null row in (table, column).

    Returns (rewritten, skipped). A row is "skipped" only when its value
    is NULL or empty; ciphertext rows are decrypted and re-encrypted so
    they end up under the current primary writer.
    """
    rows = (await session.execute(text(f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL"))).fetchall()

    rewritten = 0
    skipped = 0
    for row_id, value in rows:
        if value is None or value == "":
            skipped += 1
            continue
        # Decrypt via the keyring (any accepted reader works), then
        # re-encrypt via the primary writer.
        try:
            plaintext = encryption_service.decrypt(value)
        except Exception as exc:
            print(
                f"ERROR: {table}.{column} id={row_id}: cannot decrypt with current keyring ({exc}). "
                "Add the missing reader key to BIOAF_ENCRYPTION_KEYS and re-run.",
                file=sys.stderr,
            )
            raise SystemExit(2) from exc
        new_cipher = encryption_service.encrypt(plaintext)
        if not dry_run:
            await session.execute(
                text(f"UPDATE {table} SET {column} = :v WHERE id = :id"),
                {"v": new_cipher, "id": row_id},
            )
        rewritten += 1
    return rewritten, skipped


async def _rotate_platform_config(session: AsyncSession, dry_run: bool) -> tuple[int, int]:
    if not SENSITIVE_PLATFORM_CONFIG_KEYS:
        return 0, 0
    rows = (
        await session.execute(
            text(
                "SELECT id, key, value FROM platform_config "
                "WHERE key = ANY(:keys) AND value IS NOT NULL AND value != ''"
            ).bindparams(keys=list(SENSITIVE_PLATFORM_CONFIG_KEYS))
        )
    ).fetchall()

    rewritten = 0
    for row_id, key, value in rows:
        try:
            plaintext = encryption_service.decrypt(value)
        except Exception as exc:
            print(
                f"ERROR: platform_config.{key} id={row_id}: cannot decrypt with current keyring ({exc}).",
                file=sys.stderr,
            )
            raise SystemExit(2) from exc
        new_cipher = encryption_service.encrypt(plaintext)
        if not dry_run:
            await session.execute(
                text("UPDATE platform_config SET value = :v WHERE id = :id"),
                {"v": new_cipher, "id": row_id},
            )
        rewritten += 1
    return rewritten, 0


async def _main(dry_run: bool) -> None:
    validate_encryption_keys(settings.encryption_keys)
    print(f"Rotating encryption under primary writer (keyring has {len(settings.encryption_keys.split(','))} key(s))")

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    total = 0
    async with factory() as session:
        for table, column in _ENCRYPTED_COLUMNS:
            rewritten, skipped = await _rotate_table_column(session, table, column, dry_run)
            print(f"  {table}.{column}: rewrote {rewritten}, skipped {skipped}")
            total += rewritten

        pc_rewritten, _ = await _rotate_platform_config(session, dry_run)
        print(f"  platform_config (sensitive keys): rewrote {pc_rewritten}")
        total += pc_rewritten

        if not dry_run:
            await session.commit()

    await engine.dispose()

    mode = "would rewrite" if dry_run else "rewrote"
    print(f"\nDone. {mode} {total} rows under the current primary key.")
    if dry_run:
        print("(--dry-run: no changes persisted.)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Decrypt every row to prove the keyring covers it, but do not write back.",
    )
    args = parser.parse_args()
    asyncio.run(_main(args.dry_run))


if __name__ == "__main__":
    main()
