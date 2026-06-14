"""GKE realization of the PodIdentity seam (Stage 4c).

Maps a GCP service-account email to the GKE Workload Identity KSA annotation.
Relocated here from the three runner kubernetes providers (compute / notebooks /
cellxgene) so they name no cloud when binding pod identity.
"""

from __future__ import annotations

from app.adapters.pod_identity.base import PodIdentityProvider

# The KSA annotation that binds a GKE pod's service account to a GCP service
# account via Workload Identity.
WORKLOAD_IDENTITY_ANNOTATION = "iam.gke.io/gcp-service-account"


class GkePodIdentityProvider(PodIdentityProvider):
    """GKE Workload Identity: annotate the KSA with the bound GSA email."""

    def pod_identity_annotations(self, identity: str) -> dict[str, str]:
        if not identity:
            return {}
        return {WORKLOAD_IDENTITY_ANNOTATION: identity}
