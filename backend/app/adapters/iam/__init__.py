"""IAM provider factory (Phase 9B).

Selected by a config-keyed factory (default GCP), not the DB-backed registry:
the orphan sweep manages service accounts as a platform concern. Callers obtain
credentials and pass them through; the ``google.cloud.iam_admin_v1`` import lives
only inside the GCP implementation.
"""

from __future__ import annotations

from app.adapters.iam.base import IamProvider

VALID_IAM_BACKENDS = ("gcp",)
DEFAULT_IAM_BACKEND = "gcp"


def create_iam_provider(credentials=None, backend: str = DEFAULT_IAM_BACKEND) -> IamProvider:
    """Instantiate the IAM provider for ``backend`` (default GCP)."""
    if backend not in VALID_IAM_BACKENDS:
        raise ValueError(f"Unknown IAM backend '{backend}'. Valid options: {VALID_IAM_BACKENDS}")
    from app.adapters.iam.gcp import GcpIamProvider

    return GcpIamProvider(credentials=credentials)
