"""PodIdentity provider seam (Stage 4c).

The backend-aware seam for binding a workload pod's Kubernetes service account
(KSA) to a cloud IAM identity, so pods get cloud credentials without a mounted
key. It supplies the KSA annotations that wire that binding, plus an out-of-band
``associate`` hook for clouds whose binding is an API call rather than an
annotation.

GCP (GKE Workload Identity) annotates the KSA with
``iam.gke.io/gcp-service-account: <gsa-email>``; no association call is needed
(``associate`` is a no-op). AWS (Stage 6e) has two shapes behind this same
interface: IRSA annotates ``eks.amazonaws.com/role-arn: <role-arn>``, while EKS
Pod Identity (recommended for new clusters) sets NO annotation and instead makes
a ``CreatePodIdentityAssociation`` API call - hence the seam must allow an EMPTY
annotation dict plus the ``associate`` hook.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class PodIdentityProvider(ABC):
    """Cloud-specific pod -> cloud-IAM binding (KSA annotations + association)."""

    @abstractmethod
    def pod_identity_annotations(self, identity: str) -> dict[str, str]:
        """KSA annotations binding the pod's service account to ``identity``.

        ``identity`` is the cloud principal (GCP GSA email; AWS role ARN). An
        empty / falsy identity yields an empty dict (no binding), matching the
        historical "only annotate when an SA email is configured" behavior.
        Clouds that bind via an API call (EKS Pod Identity) return an empty dict
        here and do the work in ``associate``.
        """

    def associate(self, identity: str, namespace: str, ksa: str) -> None:
        """Out-of-band pod -> cloud-identity association.

        No-op for annotation-based identity (GKE Workload Identity, AWS IRSA),
        where ``pod_identity_annotations`` carries the whole binding. EKS Pod
        Identity overrides this to create the association via the AWS API.
        """
        return None
