"""Tests for the at-rest encryption service.

The service wraps cryptography.fernet.MultiFernet so the rest of the app
can call simple encrypt(plaintext) / decrypt(ciphertext) helpers. It is
constructed eagerly from BIOAF_ENCRYPTION_KEYS so misconfiguration fails
fast at import time, not at the moment of the first write.
"""

from __future__ import annotations

import importlib

import pytest
from cryptography.fernet import Fernet


KEY_A = "yQWeSjhut-D91YUcqvDUfQ62wQHNq1G3vUstCSJpk9U="
KEY_B = "RULBtMyNqzJbIBpDe1gwY2YCCYkBI0UqjJsdAP-41AU="


def _service_with_keys(raw_keys: str):
    """Reload the module so it picks up the patched settings."""
    from app import config

    config.settings.encryption_keys = raw_keys
    from app.platform import encryption_service as svc

    importlib.reload(svc)
    return svc


def test_round_trip_with_single_key(monkeypatch):
    svc = _service_with_keys(KEY_A)
    cipher = svc.encrypt("super-secret")
    assert cipher is not None
    assert cipher != "super-secret"
    # Fernet tokens are urlsafe-base64 and start with "gAAAA" (version byte 0x80).
    assert cipher.startswith("gAAAA")
    assert svc.decrypt(cipher) == "super-secret"


def test_none_passthrough(monkeypatch):
    svc = _service_with_keys(KEY_A)
    assert svc.encrypt(None) is None
    assert svc.decrypt(None) is None


def test_empty_string_round_trip(monkeypatch):
    svc = _service_with_keys(KEY_A)
    cipher = svc.encrypt("")
    assert cipher is not None
    assert svc.decrypt(cipher) == ""


def test_multi_key_reads_legacy_writes_with_primary(monkeypatch):
    # Write a token under key A
    a_token = Fernet(KEY_A.encode()).encrypt(b"legacy-secret").decode()

    # Init the service with B primary, A secondary (rotation state)
    svc = _service_with_keys(f"{KEY_B},{KEY_A}")
    assert svc.decrypt(a_token) == "legacy-secret"

    new_token = svc.encrypt("fresh-secret")
    # New token must be readable by Fernet(KEY_B), not by Fernet(KEY_A) alone.
    assert Fernet(KEY_B.encode()).decrypt(new_token.encode()) == b"fresh-secret"
    with pytest.raises(Exception):
        Fernet(KEY_A.encode()).decrypt(new_token.encode())


def test_retiring_old_key_breaks_legacy_reads(monkeypatch):
    # Token written under A is unreadable once A is removed from the keyring.
    a_token = Fernet(KEY_A.encode()).encrypt(b"legacy-secret").decode()

    svc = _service_with_keys(KEY_B)
    with pytest.raises(Exception):
        svc.decrypt(a_token)


def test_init_fails_when_no_keys_configured(monkeypatch):
    from app import config

    config.settings.encryption_keys = ""
    from app.platform import encryption_service as svc

    with pytest.raises(SystemExit):
        importlib.reload(svc)


def test_init_fails_on_invalid_key(monkeypatch):
    from app import config

    # Wrong length / not urlsafe-base64
    config.settings.encryption_keys = "not-a-valid-fernet-key"
    from app.platform import encryption_service as svc

    with pytest.raises(SystemExit):
        importlib.reload(svc)


def test_whitespace_is_tolerated_in_keys(monkeypatch):
    svc = _service_with_keys(f"  {KEY_A}  ,  {KEY_B}  ")
    assert svc.decrypt(svc.encrypt("hello")) == "hello"


def test_looks_like_ciphertext_detects_fernet_tokens():
    svc = _service_with_keys(KEY_A)
    cipher = svc.encrypt("payload")
    assert svc.looks_like_ciphertext(cipher) is True
    assert svc.looks_like_ciphertext("plaintext") is False
    assert svc.looks_like_ciphertext(None) is False
    assert svc.looks_like_ciphertext("") is False
