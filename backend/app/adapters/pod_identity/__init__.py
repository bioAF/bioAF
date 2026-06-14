"""PodIdentity provider factory (Stage 4c).

The backend-aware seam for binding workload pods to a cloud IAM identity.
Selected from ``cloud_provider`` like the other substrate seams:
``get_pod_identity_provider`` reads the resolved-backend cache
(``backend_for('pod_identity')``), defaulting to GKE on an unconfigured / GCP
install. AWS (EKS: IRSA annotation, or Pod Identity association API) adds an
``eks`` branch here behind the same interface in Stage 6e.
"""

from __future__ import annotations

from app.adapters.pod_identity.base import PodIdentityProvider
from app.exceptions import ValidationError

VALID_POD_IDENTITY_BACKENDS = ("gke",)
DEFAULT_POD_IDENTITY_BACKEND = "gke"


def create_pod_identity_provider(backend: str = DEFAULT_POD_IDENTITY_BACKEND) -> PodIdentityProvider:
    """Instantiate the pod-identity provider for ``backend`` (default GKE)."""
    if backend not in VALID_POD_IDENTITY_BACKENDS:
        raise ValidationError(f"Unknown pod_identity backend '{backend}'. Valid options: {VALID_POD_IDENTITY_BACKENDS}")
    from app.adapters.pod_identity.gcp import GkePodIdentityProvider

    return GkePodIdentityProvider()


def get_pod_identity_provider() -> PodIdentityProvider:
    """Resolve the pod-identity provider for this install's cloud_provider.

    Reads the resolved-backend cache (loaded at adapter init); falls back to the
    GKE default when the cache is unloaded (pre-DB bootstrap, local dev, tests),
    so behavior is unchanged on a GCP install.
    """
    from app.platform.cloud_provider import backend_for

    return create_pod_identity_provider(backend_for("pod_identity"))
