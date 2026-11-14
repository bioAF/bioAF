"""Truthful S3 link lifetimes from the ambient credentials (owner-accepted, plan 24 decision 6).

A presigned S3 URL stops working when the credentials that signed it expire, whatever its
``X-Amz-Expires`` says. Instance-profile credentials rotate on the provider's schedule, and EC2
only promises replacements five minutes before expiry, so a link signed from them can die long
before the lifetime it advertises.

This shim reads the expiry botocore keeps privately and pairs it with the exact credentials used
to sign. It is the one place outside the SDK that touches these internals, written against the
locked botocore (``TESTED_BOTOCORE_VERSION``; a test pins it so an upgrade re-validates the shim):

- ``RefreshableCredentials._refresh_lock`` guards ``_expiry_time`` and ``_frozen_credentials``.
- ``_protected_refresh(is_mandatory=True)`` refreshes from the provider; it must run under the lock.

If the remaining validity cannot cover the required lifetime, the shim refreshes once and
re-checks; a provider that returns the same short credentials, a failed refresh, an unknown expiry
on a session token, or missing internals all refuse with ``StorageUnavailableError``. Nothing
retries or waits for rotation. botocore's IMDS cache-expiration extension can make a nearly expired
credential report up to ~20 minutes of validity; that is far below the 1 h minimum, so it can only
cause a refusal, never an over-long link. Credentials never leave the adapter or reach a log.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.exceptions import StorageUnavailableError

logger = logging.getLogger("bioaf.adapters.storage.s3")

TESTED_BOTOCORE_VERSION = "1.43.126"
# Signing credentials must outlive the link by this much.
SAFETY_MARGIN_SECONDS = 60
# Signing, the audit commit and the response all happen inside this, after the expiry is measured.
HANDOFF_SECONDS = 60

_REFUSED = (
    "Download links are not available right now: the server's storage credentials are close to "
    "rotating. Try again later, or download the export directly."
)


@dataclass(frozen=True)
class SigningSnapshot:
    """One immutable key/secret/token and the moment they stop working (None: they do not expire)."""

    access_key: str
    secret_key: str = field(repr=False)
    token: str | None = field(repr=False)
    expires_at: datetime | None

    def client_kwargs(self) -> dict:
        kwargs = {"aws_access_key_id": self.access_key, "aws_secret_access_key": self.secret_key}
        if self.token:
            kwargs["aws_session_token"] = self.token
        return kwargs


def _aware(value) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _remaining(expires_at: datetime) -> float:
    return (expires_at - datetime.now(timezone.utc)).total_seconds()


def signing_snapshot(credentials, *, required_seconds: float) -> SigningSnapshot:
    """Credentials valid for ``required_seconds`` more, paired with their expiry, or a refusal."""
    from botocore.credentials import RefreshableCredentials

    if credentials is None:
        raise StorageUnavailableError(_REFUSED)
    try:
        frozen = credentials.get_frozen_credentials()  # initializes deferred credentials
    except Exception as exc:
        logger.warning("Signing credentials unavailable (%s)", type(exc).__name__)
        raise StorageUnavailableError(_REFUSED) from None

    if not isinstance(credentials, RefreshableCredentials):
        if frozen.token:
            logger.warning("Signing refused: a session token with no known expiry")
            raise StorageUnavailableError(_REFUSED)
        return SigningSnapshot(frozen.access_key, frozen.secret_key, None, None)

    lock = getattr(credentials, "_refresh_lock", None)
    refresh = getattr(credentials, "_protected_refresh", None)
    if lock is None or refresh is None or not hasattr(credentials, "_expiry_time"):
        logger.warning("Signing refused: the SDK's credential internals are not as tested")
        raise StorageUnavailableError(_REFUSED)

    with lock:
        expires_at = _aware(credentials._expiry_time)
        frozen = getattr(credentials, "_frozen_credentials", None)
        if expires_at is None or frozen is None or _remaining(expires_at) < required_seconds:
            try:
                refresh(is_mandatory=True)
            except Exception as exc:
                logger.warning("Signing refused: credential refresh failed (%s)", type(exc).__name__)
                raise StorageUnavailableError(_REFUSED) from None
            expires_at = _aware(credentials._expiry_time)
            frozen = getattr(credentials, "_frozen_credentials", None)
        if expires_at is None or frozen is None or _remaining(expires_at) < required_seconds:
            logger.warning("Signing refused: credentials too close to expiry after refresh")
            raise StorageUnavailableError(_REFUSED)
        return SigningSnapshot(frozen.access_key, frozen.secret_key, frozen.token, expires_at)


def link_lifetime(snapshot: SigningSnapshot, *, max_ttl_seconds: int, min_ttl_seconds: int) -> int:
    """Seconds the link may live: the maximum, or less to stay inside the credentials' validity."""
    if snapshot.expires_at is None:
        lifetime = max_ttl_seconds
    else:
        lifetime = min(max_ttl_seconds, int(_remaining(snapshot.expires_at)) - SAFETY_MARGIN_SECONDS)
    if lifetime < min_ttl_seconds + HANDOFF_SECONDS:
        raise StorageUnavailableError(_REFUSED)
    return lifetime
