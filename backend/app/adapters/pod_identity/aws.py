"""EKS realization of the PodIdentity seam (Stage 6e), IRSA flavor.

Maps an IAM role ARN to the IRSA KSA annotation
``eks.amazonaws.com/role-arn: <role-arn>``. This is the annotation-based binding
(the direct analog of GKE Workload Identity's ``iam.gke.io/gcp-service-account``
annotation), so ``associate`` stays a no-op. EKS Pod Identity (the
association-API flavor) would be a separate provider; IRSA is chosen because the
compute module provisions IRSA roles + the cluster OIDC provider.
"""

from __future__ import annotations

from app.adapters.pod_identity.base import PodIdentityProvider

# The KSA annotation that binds an EKS pod's service account to an IAM role via
# IRSA (IAM Roles for Service Accounts).
IRSA_ROLE_ANNOTATION = "eks.amazonaws.com/role-arn"


class EksIrsaPodIdentityProvider(PodIdentityProvider):
    """EKS IRSA: annotate the KSA with the bound IAM role ARN."""

    def pod_identity_annotations(self, identity: str) -> dict[str, str]:
        if not identity:
            return {}
        return {IRSA_ROLE_ANNOTATION: identity}
