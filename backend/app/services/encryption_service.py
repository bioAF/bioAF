"""At-rest encryption for sensitive DB columns.

Wraps cryptography.fernet.MultiFernet so callers (TypeDecorators,
PlatformConfigService, Alembic backfills) can use one shape:

    ciphertext = encrypt(plaintext)
    plaintext  = decrypt(ciphertext)

Keys come from BIOAF_ENCRYPTION_KEYS (comma-separated). The first key is the
primary writer; all keys are accepted readers. Misconfiguration fails fast at
import time via app.config.validate_encryption_keys.

The Fernet token version byte (0x80) base64-encodes to "gAAAA", so
looks_like_ciphertext() lets the backfill migration distinguish already-
encrypted rows from plaintext on re-runs.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, MultiFernet

from app.config import settings, validate_encryption_keys

FERNET_TOKEN_PREFIX = "gAAAA"

_keys = validate_encryption_keys(settings.encryption_keys)
_multifernet = MultiFernet([Fernet(k.encode()) for k in _keys])


def encrypt(plaintext: str | None) -> str | None:
    """Encrypt a string with the primary key, returning urlsafe-base64 ciphertext.

    None passes through unchanged so nullable ORM columns just work.
    """
    if plaintext is None:
        return None
    return _multifernet.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str | None) -> str | None:
    """Decrypt a Fernet token. Any key in the keyring may have produced it."""
    if ciphertext is None:
        return None
    return _multifernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")


def looks_like_ciphertext(value: str | None) -> bool:
    """Cheap detector used by the backfill migration to skip already-encrypted rows.

    Fernet tokens are urlsafe-base64 of (version || timestamp || iv || ciphertext || hmac).
    The version byte is 0x80, which base64-encodes to "gAAAA". Cheap and
    sufficient as an idempotency guard; not a substitute for an actual decrypt.
    """
    if not value:
        return False
    return value.startswith(FERNET_TOKEN_PREFIX)
