"""Billing provider factory (Phase 9D).

Selected by a config-keyed factory (default GCP), not the DB-backed registry:
historical billing is a platform concern. Callers obtain credentials and pass
them through; the ``google.cloud.bigquery`` import lives only inside the GCP
implementation.
"""

from __future__ import annotations

from app.adapters.billing.base import BillingProvider

VALID_BILLING_BACKENDS = ("gcp",)
DEFAULT_BILLING_BACKEND = "gcp"


def create_billing_provider(credentials=None, backend: str = DEFAULT_BILLING_BACKEND) -> BillingProvider:
    """Instantiate the billing provider for ``backend`` (default GCP)."""
    if backend not in VALID_BILLING_BACKENDS:
        raise ValueError(f"Unknown billing backend '{backend}'. Valid options: {VALID_BILLING_BACKENDS}")
    from app.adapters.billing.gcp import GcpBillingProvider

    return GcpBillingProvider(credentials=credentials)
