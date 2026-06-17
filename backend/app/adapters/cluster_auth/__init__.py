"""ClusterAuth provider factory (Stage 4a).

The backend-aware seam for out-of-cluster managed-Kubernetes control-plane auth.
Selected from ``cloud_provider`` like the other substrate seams:
``get_cluster_auth_provider`` reads the resolved-backend cache
(``backend_for('cluster_auth')``), defaulting to GKE on an unconfigured / GCP
install. AWS (EKS: ``aws eks get-token`` STS token + access entries) adds an
``eks`` branch here behind the same interface in Stage 6e.
"""

from __future__ import annotations

from app.adapters.cluster_auth.base import ClusterAuthProvider
from app.exceptions import ValidationError

VALID_CLUSTER_AUTH_BACKENDS = ("gke", "eks")
DEFAULT_CLUSTER_AUTH_BACKEND = "gke"


def create_cluster_auth_provider(backend: str = DEFAULT_CLUSTER_AUTH_BACKEND) -> ClusterAuthProvider:
    """Instantiate the cluster-auth provider for ``backend`` (default GKE)."""
    if backend not in VALID_CLUSTER_AUTH_BACKENDS:
        raise ValidationError(f"Unknown cluster_auth backend '{backend}'. Valid options: {VALID_CLUSTER_AUTH_BACKENDS}")
    if backend == "eks":
        from app.adapters.cluster_auth.aws import EksClusterAuthProvider

        return EksClusterAuthProvider()
    from app.adapters.cluster_auth.gcp import GkeClusterAuthProvider

    return GkeClusterAuthProvider()


def get_cluster_auth_provider() -> ClusterAuthProvider:
    """Resolve the cluster-auth provider for this install's cloud_provider.

    Reads the resolved-backend cache (loaded at adapter init); falls back to the
    GKE default when the cache is unloaded (pre-DB bootstrap, local dev, tests),
    so behavior is unchanged on a GCP install.
    """
    from app.platform.cloud_provider import backend_for

    return create_cluster_auth_provider(backend_for("cluster_auth"))
