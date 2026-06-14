"""ClusterAuth provider seam (Stage 4a).

The backend-aware seam for authenticating to a managed-Kubernetes control plane
from OUTSIDE the cluster (the standalone-VM topology). It supplies the three
cloud-specific inputs the shared connection needs - the API-server endpoint, the
cluster CA cert, and a bearer token - plus the token-refresh TTL. The neutral
connection plumbing (CA-to-tempfile, ``Configuration`` assembly, in-cluster-first
fallback, cached-client refresh) stays in ``adapters/kubernetes/connection.py``.

GCP (GKE) mints a google-auth OAuth token and reads ``gke_cluster_endpoint`` /
``gke_cluster_ca_cert`` from platform_config. AWS (EKS, Stage 6e) mints an
``aws eks get-token`` STS presigned-URL token (``k8s-aws-v1.`` ExecCredential,
~15-min TTL) and derives the endpoint/CA from the cluster description, behind
this same interface. (AWS docs confirm out-of-cluster IAM auth is fully
supported; RBAC is wired via EKS access entries.)
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class ClusterAuthProvider(ABC):
    """Cloud-specific control-plane endpoint + CA + bearer-token resolution."""

    # How long a minted bearer token stays usable before the connection rebuilds.
    # GCP OAuth tokens last ~3600s (refresh at 2700s); EKS STS tokens ~900s.
    token_ttl_seconds: int = 2700

    @abstractmethod
    def cluster_endpoint(self, cluster_config: dict) -> str:
        """Raw control-plane endpoint from cluster_config ("" if unset)."""

    @abstractmethod
    def cluster_ca_cert(self, cluster_config: dict) -> str:
        """Raw base64-encoded cluster CA cert from cluster_config ("" if unset)."""

    @abstractmethod
    def bearer_token(self, cluster_config: dict) -> str:
        """Mint a bearer token for the Kubernetes API server."""
