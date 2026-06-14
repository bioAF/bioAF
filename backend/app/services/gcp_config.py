"""GCP credentials validation (re-export shim).

The validation routine and its google-cloud SDK clients (``resourcemanager_v3``,
``storage``, ``service_usage_v1``, ``container_v1``) moved to
``app.adapters.validation.gcp`` in Stage 3b so the service layer holds no cloud
SDK. This module re-exports the public surface so existing imports
(``from app.services.gcp_config import validate_gcp_credentials``) keep working
unchanged.

Tests that previously patched ``app.services.gcp_config.<sdk_client>`` now patch
``app.adapters.validation.gcp.<sdk_client>`` (that is where the clients live).
"""

from __future__ import annotations

from app.adapters.validation.gcp import (
    APP_ROLES,
    BOOTSTRAP_ROLES,
    RECOMMENDED_ROLES,
    validate_gcp_credentials,
    _APP_PERMS,
    _BOOTSTRAP_PERMS,
    _DROPPED_PERMS,
    _SHARED_PERMS,
)

__all__ = [
    "validate_gcp_credentials",
    "APP_ROLES",
    "BOOTSTRAP_ROLES",
    "RECOMMENDED_ROLES",
    "_APP_PERMS",
    "_BOOTSTRAP_PERMS",
    "_DROPPED_PERMS",
    "_SHARED_PERMS",
]
