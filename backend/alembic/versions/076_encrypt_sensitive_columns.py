"""Backfill ciphertext into sensitive columns; widen storage where needed.

Revision ID: 076
Revises: 075
Create Date: 2026-05-12

Encrypts every column flagged in spec-encryption-at-rest:
- organizations.{smtp_password, slack_client_secret, slack_signing_secret}
- session_credentials.ssh_private_key
- compute_sessions.heartbeat_token
- slack_installations.bot_token
- slack_webhooks.webhook_url
- platform_config rows where key in SENSITIVE_PLATFORM_CONFIG_KEYS

Migration aborts cleanly if BIOAF_ENCRYPTION_KEYS is unset rather than
corrupting rows. Already-encrypted rows (Fernet tokens start with "gAAAA")
are skipped so the migration is safely idempotent.
"""

from __future__ import annotations

import sys

import sqlalchemy as sa
from alembic import op

from app.config import settings, validate_encryption_keys
from app.services import encryption_service
from app.services.platform_config_service import SENSITIVE_PLATFORM_CONFIG_KEYS

revision = "076"
down_revision = "075"
branch_labels = None
depends_on = None


# (table, column, widen_to_text)
# widen_to_text is True when the existing column is a String(N) that may not
# fit Fernet ciphertext after backfill. Heartbeat token and bot_token sit on
# String(255)/String(500) today, comfortably under a single Fernet token for
# their plaintext, but ciphertext grows the payload enough to warrant TEXT.
_ENCRYPTED_COLUMNS: list[tuple[str, str, bool]] = [
    ("organizations", "smtp_password", True),
    ("organizations", "slack_client_secret", True),
    ("organizations", "slack_signing_secret", True),
    ("session_credentials", "ssh_private_key", False),
    ("compute_sessions", "heartbeat_token", True),
    ("slack_installations", "bot_token", True),
    ("slack_webhooks", "webhook_url", True),
]


def _ensure_keys_configured() -> None:
    if not settings.encryption_keys:
        print(
            "FATAL: BIOAF_ENCRYPTION_KEYS is not set. Refusing to run the "
            "at-rest encryption migration with no key configured -- rows "
            "would be corrupted. Generate a Fernet key, set the env var, "
            "and re-run.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    validate_encryption_keys(settings.encryption_keys)


def upgrade() -> None:
    _ensure_keys_configured()

    # 1. Widen columns whose current String(N) is too small for ciphertext.
    for table, column, widen in _ENCRYPTED_COLUMNS:
        if widen:
            op.alter_column(table, column, type_=sa.Text(), existing_nullable=True)

    bind = op.get_bind()

    # 2. Backfill each encrypted column. Encrypts every non-null row whose
    # current value does not already look like Fernet ciphertext.
    for table, column, _widen in _ENCRYPTED_COLUMNS:
        rows = bind.execute(
            sa.text(f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL")
        ).fetchall()
        for row_id, value in rows:
            if value is None:
                continue
            if encryption_service.looks_like_ciphertext(value):
                continue
            ciphertext = encryption_service.encrypt(value)
            bind.execute(
                sa.text(f"UPDATE {table} SET {column} = :v WHERE id = :id"),
                {"v": ciphertext, "id": row_id},
            )

    # 3. Encrypt sensitive platform_config rows.
    if SENSITIVE_PLATFORM_CONFIG_KEYS:
        pc_rows = bind.execute(
            sa.text(
                "SELECT id, key, value FROM platform_config "
                "WHERE key = ANY(:keys) AND value IS NOT NULL"
            ).bindparams(keys=list(SENSITIVE_PLATFORM_CONFIG_KEYS))
        ).fetchall()
        for row_id, _key, value in pc_rows:
            if value is None or value == "":
                continue
            if encryption_service.looks_like_ciphertext(value):
                continue
            ciphertext = encryption_service.encrypt(value)
            bind.execute(
                sa.text("UPDATE platform_config SET value = :v WHERE id = :id"),
                {"v": ciphertext, "id": row_id},
            )


def downgrade() -> None:
    """Reverse the encryption backfill (decrypt rows in place).

    Schema-wise this is best-effort: widened columns stay TEXT (downsizing
    would risk truncating long plaintext on future writes). The point of
    downgrade here is recoverability of the data, not bit-for-bit schema
    restoration.
    """
    _ensure_keys_configured()
    bind = op.get_bind()

    for table, column, _widen in _ENCRYPTED_COLUMNS:
        rows = bind.execute(
            sa.text(f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL")
        ).fetchall()
        for row_id, value in rows:
            if value is None or not encryption_service.looks_like_ciphertext(value):
                continue
            plaintext = encryption_service.decrypt(value)
            bind.execute(
                sa.text(f"UPDATE {table} SET {column} = :v WHERE id = :id"),
                {"v": plaintext, "id": row_id},
            )

    if SENSITIVE_PLATFORM_CONFIG_KEYS:
        pc_rows = bind.execute(
            sa.text(
                "SELECT id, key, value FROM platform_config WHERE key = ANY(:keys)"
            ).bindparams(keys=list(SENSITIVE_PLATFORM_CONFIG_KEYS))
        ).fetchall()
        for row_id, _key, value in pc_rows:
            if value is None or not encryption_service.looks_like_ciphertext(value):
                continue
            plaintext = encryption_service.decrypt(value)
            bind.execute(
                sa.text("UPDATE platform_config SET value = :v WHERE id = :id"),
                {"v": plaintext, "id": row_id},
            )
