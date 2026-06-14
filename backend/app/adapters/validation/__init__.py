"""Account/credentials validation providers (Stage 3b).

GCP account validation (and its google-cloud SDK clients) lives in
``app.adapters.validation.gcp`` behind the BAL boundary. The validate flow is
cloud-specific at the API layer, so this is a relocation rather than a runtime
backend seam; an AWS validation provider is added here as a sibling later.
"""

from __future__ import annotations

from app.adapters.validation.gcp import (
    APP_ROLES,
    BOOTSTRAP_ROLES,
    RECOMMENDED_ROLES,
    validate_gcp_credentials,
)

__all__ = [
    "validate_gcp_credentials",
    "APP_ROLES",
    "BOOTSTRAP_ROLES",
    "RECOMMENDED_ROLES",
]
