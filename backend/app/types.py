"""Custom SQLAlchemy types for bioAF.

EncryptedString
    TypeDecorator that transparently encrypts column values at write time
    and decrypts at read time, so models can declare sensitive columns
    without leaking the encryption boundary into application code. Backed
    by app.services.encryption_service (Fernet / MultiFernet).
"""

from __future__ import annotations

from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.services import encryption_service


class EncryptedString(TypeDecorator):
    """Transparent at-rest encryption for string columns.

    Ciphertext is stored as urlsafe-base64 Fernet tokens in a TEXT column.
    Fernet ciphertext is meaningfully larger than the plaintext, so the
    underlying type is unbounded TEXT regardless of the logical max length
    of the plaintext.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):  # type: ignore[override]
        return encryption_service.encrypt(value)

    def process_result_value(self, value, dialect):  # type: ignore[override]
        return encryption_service.decrypt(value)
