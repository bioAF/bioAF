"""Encrypt upload session handles already stored in plain text.

Revision ID: 143
Revises: 142
Create Date: 2026-11-22

``upload_checkpoints.session_handle`` holds a provider upload session; for Google Cloud that is a
resumable session URL, a bearer credential for its upload. The model now stores it with
EncryptedString (ADR-047). This encrypts handles written in plain text by installations that ran 142
before that change. Values that already look like Fernet ciphertext are skipped, so it is idempotent.
"""

from __future__ import annotations

import sys

import sqlalchemy as sa
from alembic import op
from cryptography.fernet import Fernet, MultiFernet

# The installation's own keys, received at runtime like every other process; never frozen here.
from app.config import settings, validate_encryption_keys

revision = "143"
down_revision = "142"
branch_labels = None
depends_on = None

# Frozen here, as in 076: the transformation (Fernet with the first configured key, MultiFernet's
# writer) and the idempotency rule (a value starting with the Fernet version prefix is ciphertext).
FERNET_TOKEN_PREFIX = "gAAAA"


def _looks_like_ciphertext(value: str | None) -> bool:
    return bool(value) and value.startswith(FERNET_TOKEN_PREFIX)


def _rows(bind) -> list:
    return bind.execute(
        sa.text("SELECT id, session_handle FROM upload_checkpoints WHERE session_handle IS NOT NULL")
    ).fetchall()


def _fernet() -> MultiFernet:
    if not settings.encryption_keys:
        print(
            "FATAL: BIOAF_ENCRYPTION_KEYS is not set, so stored upload session handles cannot be encrypted.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return MultiFernet([Fernet(k.encode()) for k in validate_encryption_keys(settings.encryption_keys)])


def upgrade() -> None:
    bind = op.get_bind()
    plain = [(row_id, value) for row_id, value in _rows(bind) if value and not _looks_like_ciphertext(value)]
    if not plain:
        return
    fernet = _fernet()
    for row_id, value in plain:
        bind.execute(
            sa.text("UPDATE upload_checkpoints SET session_handle = :v WHERE id = :id"),
            {"v": fernet.encrypt(value.encode("utf-8")).decode("ascii"), "id": row_id},
        )


def downgrade() -> None:
    bind = op.get_bind()
    cipher = [(row_id, value) for row_id, value in _rows(bind) if _looks_like_ciphertext(value)]
    if not cipher:
        return
    fernet = _fernet()
    for row_id, value in cipher:
        bind.execute(
            sa.text("UPDATE upload_checkpoints SET session_handle = :v WHERE id = :id"),
            {"v": fernet.decrypt(value.encode("ascii")).decode("utf-8"), "id": row_id},
        )
