"""Credentials provider factory (Stage 3c).

The backend-aware seam for resolving cloud credentials. Selected from
``cloud_provider`` like the other substrate seams: ``get_credentials_provider``
reads the resolved-backend cache (``backend_for('credentials')``), defaulting to
GCP on an unconfigured / GCP install. AWS (STS / assume-role / instance profile)
adds an ``aws`` branch here behind the same interface later.
"""

from __future__ import annotations

from app.adapters.credentials.base import CredentialsProvider
from app.exceptions import ValidationError

VALID_CREDENTIALS_BACKENDS = ("gcp",)
DEFAULT_CREDENTIALS_BACKEND = "gcp"


def create_credentials_provider(backend: str = DEFAULT_CREDENTIALS_BACKEND) -> CredentialsProvider:
    """Instantiate the credentials provider for ``backend`` (default GCP)."""
    if backend not in VALID_CREDENTIALS_BACKENDS:
        raise ValidationError(f"Unknown credentials backend '{backend}'. Valid options: {VALID_CREDENTIALS_BACKENDS}")
    from app.adapters.credentials.gcp import GcpCredentialsProvider

    return GcpCredentialsProvider()


def get_credentials_provider() -> CredentialsProvider:
    """Resolve the credentials provider for this install's cloud_provider.

    Reads the resolved-backend cache (loaded at adapter init); falls back to the
    GCP default when the cache is unloaded (pre-DB bootstrap, local dev, tests),
    so behavior is unchanged on a GCP install.
    """
    from app.platform.cloud_provider import backend_for

    return create_credentials_provider(backend_for("credentials"))
