"""Pod-identity annotations on a runner Kubernetes service account.

The compute, notebook and cellxgene Kubernetes adapters each bind their runner KSA to a cloud
identity through annotations the ``PodIdentityProvider`` supplies (GKE Workload Identity, EKS
IRSA). On the upgrade path the KSA already exists without them, so each adapter read it, compared,
and patched; that mechanic lives here once. Resolving the identity stays with each adapter.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("bioaf.adapters.kubernetes.service_account")


def ensure_service_account_annotations(core_v1, *, namespace: str, service_account: str, desired: dict) -> None:
    """Patch ``desired`` onto the service account if any value differs; failures are logged, not raised.

    An empty ``desired`` (no cloud identity configured) does nothing.
    """
    if not desired:
        return
    try:
        sa = core_v1.read_namespaced_service_account(name=service_account, namespace=namespace)
        current = sa.metadata.annotations or {}
        if any(current.get(k) != v for k, v in desired.items()):
            core_v1.patch_namespaced_service_account(
                name=service_account,
                namespace=namespace,
                body={"metadata": {"annotations": desired}},
            )
            logger.info("Patched pod-identity annotation on %s", service_account)
    except Exception:
        logger.exception("Failed to patch pod-identity annotation on %s", service_account)
