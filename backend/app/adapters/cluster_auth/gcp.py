"""GKE realization of the ClusterAuth seam (Stage 4a).

Mints a GCP OAuth access token via credential_injector and reads the GKE cluster
endpoint / CA from platform_config. Relocated here from
``adapters/kubernetes/connection.py`` so the shared connection names no cloud.
"""

from __future__ import annotations

from app.adapters.cluster_auth.base import ClusterAuthProvider


def _get_gcp_token(cfg: dict) -> str:
    """Mint a GCP access token via credential_injector.

    Returns a Bearer token suitable for the K8s API. In vm_default mode this
    uses the node/metadata identity (optionally impersonating a bootstrap SA);
    in service_account_key mode it uses the stored key.
    """
    import google.auth.transport.requests

    from app.adapters.credentials import credential_injector

    credentials = credential_injector.load_gcp_credentials(cfg)
    credentials.refresh(google.auth.transport.requests.Request())
    return credentials.token


class GkeClusterAuthProvider(ClusterAuthProvider):
    """GKE control-plane auth: google-auth OAuth token + gke_cluster_* config."""

    token_ttl_seconds = 2700  # 45 minutes; under the ~3600s GCP token life

    def cluster_endpoint(self, cluster_config: dict) -> str:
        return cluster_config.get("gke_cluster_endpoint", "") or ""

    def cluster_ca_cert(self, cluster_config: dict) -> str:
        return cluster_config.get("gke_cluster_ca_cert", "") or ""

    def bearer_token(self, cluster_config: dict) -> str:
        return _get_gcp_token(cluster_config)
